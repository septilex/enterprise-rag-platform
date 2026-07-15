"""Background (worker-queued) uploads for large files — ING-09 bulk lane.

Large uploads are spooled to disk and ingested by the worker so the API
request returns immediately; small uploads keep the inline path.
"""

import uuid

import fakeredis

from app import worker
from app.core.config import settings
from app.db.models import Document, IngestionRun
from app.services import ingestion_runs
from app.services.jobs import RedisJobQueue
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _queue():
    return RedisJobQueue(fakeredis.FakeStrictRedis(decode_responses=True))


def _collection(api_client, tenant):
    r = api_client.post(
        "/collections", json={"tenant_id": str(tenant.id), "name": "bg-up"})
    return r.json()["id"]


def _upload(api_client, tenant, cid, content: bytes, background: str | None = None,
            filename: str = "big.txt"):
    data = {"tenant_id": str(tenant.id), "collection_id": cid}
    if background is not None:
        data["background"] = background
    return api_client.post(
        "/documents/upload", data=data,
        files={"file": (filename, content, "text/plain")})


def test_explicit_background_upload_enqueues_then_worker_ingests(
        api_client, db_session, tenant, monkeypatch, tmp_path):
    import app.api.routes as routes
    q = _queue()
    monkeypatch.setattr(routes, "_job_queue", q)
    monkeypatch.setattr(settings, "UPLOAD_SPOOL_DIR", str(tmp_path))
    cid = _collection(api_client, tenant)

    r = _upload(api_client, tenant, cid, b"vacation policy grants twenty days " * 20,
                background="true")
    assert r.status_code == 201
    body = r.json()
    assert body["background"] is True and body["document_id"] is None
    assert body["status"] == "queued" and body["run_id"]
    assert q.depth()["bulk"] == 1

    spooled = list(tmp_path.iterdir())
    assert len(spooled) == 1  # raw bytes parked on disk, not in Redis

    # Worker drains the job with fakes — full parse+chunk+embed off-path.
    job = q.dequeue()
    worker.handle_job(db_session, job, FakeEmbedder(), InMemoryVectorStore(), None, q)

    run = db_session.get(IngestionRun, uuid.UUID(body["run_id"]))
    assert run.status == "succeeded" and run.chunks_created > 0
    assert run.run_metadata.get("background") is True
    doc = (db_session.query(Document)
           .filter_by(collection_id=uuid.UUID(cid)).one())
    assert doc.status == "embedded"
    assert list(tmp_path.iterdir()) == []  # spool cleaned up on success


def test_size_threshold_auto_backgrounds(api_client, tenant, monkeypatch, tmp_path):
    import app.api.routes as routes
    q = _queue()
    monkeypatch.setattr(routes, "_job_queue", q)
    monkeypatch.setattr(settings, "UPLOAD_SPOOL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "UPLOAD_BACKGROUND_THRESHOLD_BYTES", 64)
    cid = _collection(api_client, tenant)

    r = _upload(api_client, tenant, cid, b"x" * 200)  # no explicit flag
    assert r.status_code == 201 and r.json()["background"] is True
    assert q.depth()["bulk"] == 1


def test_small_upload_stays_inline(api_client, tenant, monkeypatch, tmp_path):
    import app.api.routes as routes
    q = _queue()
    monkeypatch.setattr(routes, "_job_queue", q)
    monkeypatch.setattr(settings, "UPLOAD_SPOOL_DIR", str(tmp_path))
    cid = _collection(api_client, tenant)

    r = _upload(api_client, tenant, cid, b"small policy doc, twenty vacation days")
    assert r.status_code == 201
    body = r.json()
    assert body["background"] is False and body["status"] == "embedded"
    assert body["document_id"] and body["chunks_created"] > 0
    assert q.depth() == {"incremental": 0, "bulk": 0, "dead": 0}


def test_retry_after_interrupted_embed_reindexes_instead_of_noop(
        db_session, tenant):
    """A doc left in 'chunked' by a timeout must be re-indexed on retry.

    Regression: the idempotent no-op matched on content_hash alone, so a retry
    'succeeded' while the vector store stayed empty (found via live 2MB upload
    whose first embed attempt timed out).
    """
    from app.db.models import Collection
    from app.services import ingestion

    coll = Collection(tenant_id=tenant.id, name="bg-retry")
    db_session.add(coll); db_session.commit(); db_session.refresh(coll)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()

    doc, chunks, reused = ingestion.ingest_text_document(
        db_session, tenant_id=tenant.id, collection_id=coll.id,
        title="handbook", content="vacation policy twenty days " * 40,
        embedder=emb, vector_store=vs)
    assert doc.status == "embedded" and not reused and chunks > 0

    # Simulate an interrupted first attempt: chunks persisted, vectors lost.
    doc.status = "chunked"
    db_session.commit()
    vs.points.clear()

    doc2, chunks2, reused2 = ingestion.ingest_text_document(
        db_session, tenant_id=tenant.id, collection_id=coll.id,
        title="handbook", content="vacation policy twenty days " * 40,
        embedder=emb, vector_store=vs)
    assert doc2.id == doc.id
    assert reused2 is False          # must NOT no-op
    assert doc2.status == "embedded" and chunks2 > 0
    assert len(vs.points) > 0        # vectors actually restored


def test_unparseable_background_upload_fails_run_without_retry(
        db_session, tenant, tmp_path):
    from app.db.models import Collection
    coll = Collection(tenant_id=tenant.id, name="bg-bad")
    db_session.add(coll); db_session.commit(); db_session.refresh(coll)

    source = ingestion_runs.manual_upload_source(db_session, tenant.id, coll.id)
    run = ingestion_runs.create_queued_run(
        db_session, source, trigger_type=ingestion_runs.TRIGGER_MANUAL)
    spool = tmp_path / "broken.pdf"
    spool.write_bytes(b"%PDF-1.4 not really a pdf")

    q = _queue()
    job = {"kind": "ingest_upload", "run_id": str(run.id),
           "source_id": str(source.id), "tenant_id": str(tenant.id),
           "collection_id": str(coll.id), "spool_path": str(spool),
           "filename": "broken.pdf", "content_type": "application/pdf",
           "metadata": {}, "attempt": 0}
    worker.handle_job(db_session, job, FakeEmbedder(), InMemoryVectorStore(), None, q)

    db_session.refresh(run)
    assert run.status == "failed" and "parse failed" in run.error_summary
    # permanent input failure: no requeue, no dead-letter noise, spool removed
    assert q.depth() == {"incremental": 0, "bulk": 0, "dead": 0}
    assert not spool.exists()
