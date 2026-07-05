"""Connector framework + delta detection + sync + background worker (ING-01/02/03/09)."""

import uuid

import fakeredis

from app.db.models import Collection, IngestionRun, Source
from app.services import connectors, ingestion_runs
from app.services.jobs import RedisJobQueue
from app import worker
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="conn-coll")
    db.add(c); db.commit(); db.refresh(c)
    return c


# ---- connector delta ----

def test_s3_mock_delta_only_returns_changed():
    objs = {"a.txt": "alpha content one", "b.txt": "bravo content two"}
    conn = connectors.S3MockConnector(objects=objs)
    first = conn.fetch_delta(None)
    assert len(first.documents) == 2

    # unchanged -> no delta
    second = connectors.S3MockConnector(objects=objs).fetch_delta(first.cursor)
    assert second.documents == []

    # add + modify -> only those come back
    objs2 = {"a.txt": "alpha content CHANGED", "b.txt": "bravo content two", "c.txt": "new"}
    third = connectors.S3MockConnector(objects=objs2).fetch_delta(first.cursor)
    uris = {d.source_uri for d in third.documents}
    assert uris == {"s3://a.txt", "s3://c.txt"}


def test_filesystem_delta(tmp_path):
    (tmp_path / "one.txt").write_text("first document body", encoding="utf-8")
    conn = connectors.FilesystemConnector(root=str(tmp_path))
    r1 = conn.fetch_delta(None)
    assert len(r1.documents) == 1
    r2 = connectors.FilesystemConnector(root=str(tmp_path)).fetch_delta(r1.cursor)
    assert r2.documents == []  # nothing changed


# ---- source sync (inline) ----

def test_sync_source_is_delta_aware(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    src = ingestion_runs.get_or_create_source(
        db_session, tenant.id, coll.id, "s3_mock", "Bucket A",
        config={"connector_type": "s3_mock", "objects": {"a.txt": "alpha vacation policy"}})

    run1 = ingestion_runs.sync_source(db_session, src, emb, vs)
    assert run1.status == "succeeded" and run1.documents_indexed == 1

    # re-sync unchanged -> delta empty -> 0 seen
    run2 = ingestion_runs.sync_source(db_session, src, emb, vs)
    assert run2.documents_seen == 0

    # add an object -> next sync picks up only the new one
    cfg = dict(src.config); cfg["objects"] = {**cfg["objects"], "b.txt": "bravo sick leave"}
    src.config = cfg; db_session.commit()
    run3 = ingestion_runs.sync_source(db_session, src, emb, vs)
    assert run3.documents_indexed == 1


# ---- background worker lifecycle ----

def _queue():
    return RedisJobQueue(fakeredis.FakeStrictRedis(decode_responses=True))


def test_worker_processes_queued_run_to_succeeded(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    src = ingestion_runs.get_or_create_source(
        db_session, tenant.id, coll.id, "s3_mock", "Bucket W",
        config={"connector_type": "s3_mock", "objects": {"x.txt": "content for worker"}})
    run = ingestion_runs.create_queued_run(db_session, src, trigger_type="manual")
    assert run.status == "queued"

    q = _queue()
    q.enqueue({"kind": "sync_source", "source_id": str(src.id),
               "run_id": str(run.id), "attempt": 0})
    worker.handle_job(db_session, q.dequeue(), emb, vs, None, q)

    db_session.refresh(run)
    assert run.status == "succeeded" and run.documents_indexed == 1


def test_worker_retries_then_fails(db_session, tenant, monkeypatch):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    src = ingestion_runs.get_or_create_source(
        db_session, tenant.id, coll.id, "s3_mock", "Bucket F",
        config={"connector_type": "s3_mock", "objects": {"x.txt": "boom"}})
    run = ingestion_runs.create_queued_run(db_session, src)

    def boom(*a, **k):
        raise RuntimeError("connector exploded")
    monkeypatch.setattr(ingestion_runs, "sync_source", boom)

    q = _queue()
    job = {"kind": "sync_source", "source_id": str(src.id), "run_id": str(run.id), "attempt": 0}

    # attempt 0 -> requeue
    worker.handle_job(db_session, job, emb, vs, None, q)
    db_session.refresh(run)
    assert run.status == "queued"
    nxt = q.dequeue()
    assert nxt["attempt"] == 1

    # exhaust retries -> failed
    worker.handle_job(db_session, {**job, "attempt": worker.MAX_ATTEMPTS - 1}, emb, vs, None, q)
    db_session.refresh(run)
    assert run.status == "failed" and "exploded" in (run.error_summary or "")
