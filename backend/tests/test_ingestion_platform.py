"""Ingestion platform foundation: sources, runs, provenance (ING-01/02/07)."""

from app.db.models import Collection, Document, IngestionRun, Source
from app.services import ingestion_runs
from tests.fakes import FakeEmbedder, InMemoryVectorStore

LONG = "Vacation policy grants twenty paid days per year. " * 4
BINARY = "\x00\x01\x02\x03\x04\x05\x06\x07\x08"


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="ing-coll")
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_ingest_items_creates_source_run_and_provenance(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    src = ingestion_runs.manual_upload_source(db_session, tenant.id, coll.id)
    assert src.source_type == "manual_upload"

    run, results = ingestion_runs.ingest_items(
        db_session, tenant.id, coll.id, src,
        items=[{"title": "policy.txt", "content": LONG,
                "source_uri": "upload://policy.txt"}],
        embedder=emb, vector_store=vs)

    assert run.status == "succeeded"
    assert run.documents_seen == 1 and run.documents_indexed == 1
    assert run.chunks_created > 0 and run.documents_quarantined == 0

    doc, count, reused = results[0]
    assert doc.source_id == src.id           # provenance: source
    assert doc.ingestion_run_id == run.id    # provenance: run
    assert db_session.get(Document, doc.id).source_id == src.id


def test_manual_source_is_reused_idempotently(db_session, tenant):
    coll = _coll(db_session, tenant)
    s1 = ingestion_runs.manual_upload_source(db_session, tenant.id, coll.id)
    s2 = ingestion_runs.manual_upload_source(db_session, tenant.id, coll.id)
    assert s1.id == s2.id
    assert db_session.query(Source).filter_by(collection_id=coll.id).count() == 1


def test_reingest_same_content_records_reused_run(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    src = ingestion_runs.manual_upload_source(db_session, tenant.id, coll.id)
    item = [{"title": "p.txt", "content": LONG, "source_uri": "upload://p.txt"}]

    ingestion_runs.ingest_items(db_session, tenant.id, coll.id, src, item, emb, vs)
    run2, results2 = ingestion_runs.ingest_items(
        db_session, tenant.id, coll.id, src, item, emb, vs)

    assert run2.status == "succeeded"
    assert run2.documents_indexed == 1
    assert results2[0][2] is True            # reused
    # two runs recorded against the one source
    assert db_session.query(IngestionRun).filter_by(source_id=src.id).count() == 2


def test_quarantined_upload_marks_run_failed(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    src = ingestion_runs.manual_upload_source(db_session, tenant.id, coll.id)

    run, _ = ingestion_runs.ingest_items(
        db_session, tenant.id, coll.id, src,
        items=[{"title": "bad.bin", "content": BINARY}],
        embedder=emb, vector_store=vs)

    assert run.documents_quarantined == 1 and run.documents_indexed == 0
    assert run.status == "failed"
    assert run.error_summary and "quarantined" in run.error_summary


# --- HTTP layer: upload flows through the framework + operator visibility ---

def _api_coll(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "ops"}).json()["id"]
    return tid, cid


def test_upload_creates_source_and_run_visible_via_api(api_client, tenant):
    tid, cid = _api_coll(api_client, tenant)
    r = api_client.post("/documents/upload",
                        data={"tenant_id": tid, "collection_id": cid},
                        files={"file": ("policy.txt", LONG.encode(), "text/plain")})
    assert r.status_code == 201 and r.json()["status"] == "embedded"

    sources = api_client.get("/sources", params={"tenant_id": tid, "collection_id": cid}).json()
    assert any(s["source_type"] == "manual_upload" for s in sources)

    runs = api_client.get("/ingestion/runs", params={"tenant_id": tid, "collection_id": cid}).json()
    assert runs and runs[0]["status"] == "succeeded"
    assert runs[0]["documents_indexed"] == 1 and runs[0]["chunks_created"] > 0

    docs = api_client.get("/documents", params={"tenant_id": tid, "collection_id": cid}).json()
    assert docs[0]["source_id"] is not None   # doc tied back to its source


def test_quarantined_upload_visible_as_failed_run(api_client, tenant):
    tid, cid = _api_coll(api_client, tenant)
    api_client.post("/documents/upload",
                    data={"tenant_id": tid, "collection_id": cid},
                    files={"file": ("bad.bin", BINARY.encode(), "application/octet-stream")})
    runs = api_client.get("/ingestion/runs", params={"tenant_id": tid, "collection_id": cid}).json()
    assert runs[0]["status"] == "failed" and runs[0]["documents_quarantined"] == 1


def test_source_disable_and_reindex(api_client, tenant):
    tid, cid = _api_coll(api_client, tenant)
    api_client.post("/documents/upload",
                    data={"tenant_id": tid, "collection_id": cid},
                    files={"file": ("policy.txt", LONG.encode(), "text/plain")})
    src = api_client.get("/sources", params={"tenant_id": tid, "collection_id": cid}).json()[0]
    sid = src["id"]

    # disable
    r = api_client.patch(f"/sources/{sid}", params={"tenant_id": tid}, json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False

    # reindex -> a reindex-trigger run that re-embeds existing chunks
    r = api_client.post(f"/sources/{sid}/reindex", params={"tenant_id": tid})
    assert r.status_code == 200
    run = r.json()
    assert run["trigger_type"] == "reindex" and run["status"] == "succeeded"
    assert run["documents_indexed"] == 1 and run["chunks_created"] > 0
