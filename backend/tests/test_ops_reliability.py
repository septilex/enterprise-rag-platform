"""Enterprise ops completeness: deletion delta, DLQ, heartbeat, reaper,
idempotent rerun, retry, system status."""

import time
import uuid

import fakeredis

from app.db.models import Collection, Document
from app.services import connectors, ingestion_runs
from app.services.jobs import RedisJobQueue
from app import worker
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="ops-coll")
    db.add(c); db.commit(); db.refresh(c)
    return c


# ---- connector deletion realism ----

def test_s3_delta_reports_deletions():
    objs = {"a.txt": "alpha", "b.txt": "bravo"}
    r1 = connectors.S3MockConnector(objects=objs).fetch_delta(None)
    # remove b.txt
    r2 = connectors.S3MockConnector(objects={"a.txt": "alpha"}).fetch_delta(r1.cursor)
    assert r2.deleted == ["s3://b.txt"] and r2.documents == []


def test_sync_propagates_deletion(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    src = ingestion_runs.get_or_create_source(
        db_session, tenant.id, coll.id, "s3_mock", "Bucket D",
        config={"connector_type": "s3_mock", "objects": {"a.txt": "alpha doc", "b.txt": "bravo doc"}})
    ingestion_runs.sync_source(db_session, src, emb, vs)
    assert db_session.query(Document).filter_by(collection_id=coll.id).count() == 2

    # delete b.txt from the bucket, re-sync -> deletion propagated
    cfg = dict(src.config); cfg["objects"] = {"a.txt": "alpha doc"}
    src.config = cfg; db_session.commit()
    run = ingestion_runs.sync_source(db_session, src, emb, vs)
    assert run.documents_deleted == 1
    assert db_session.query(Document).filter_by(collection_id=coll.id).count() == 1


# ---- reliability primitives ----

def test_stuck_run_reaper(db_session, tenant):
    coll = _coll(db_session, tenant)
    src = ingestion_runs.get_or_create_source(
        db_session, tenant.id, coll.id, "s3_mock", "B",
        config={"connector_type": "s3_mock", "objects": {}})
    run = ingestion_runs.create_queued_run(db_session, src)
    # backdate it so it looks stuck
    from datetime import datetime, timedelta, timezone
    run.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    n = ingestion_runs.recover_stuck_runs(db_session, timeout_seconds=900)
    db_session.refresh(run)
    assert n == 1 and run.status == "failed" and "recovered" in run.error_summary


def test_idempotent_active_run_guard(db_session, tenant):
    coll = _coll(db_session, tenant)
    src = ingestion_runs.get_or_create_source(
        db_session, tenant.id, coll.id, "s3_mock", "B2",
        config={"connector_type": "s3_mock", "objects": {}})
    r = ingestion_runs.create_queued_run(db_session, src)
    assert ingestion_runs.active_run_for_source(db_session, src.id).id == r.id


def test_dlq_and_heartbeat():
    q = RedisJobQueue(fakeredis.FakeStrictRedis(decode_responses=True))
    q.dead_letter({"kind": "sync_source", "source_id": "x"})
    assert q.depth()["dead"] == 1 and len(q.dead_letters()) == 1
    assert q.last_heartbeat() is None
    q.heartbeat()
    assert q.last_heartbeat() is not None and (time.time() - q.last_heartbeat()) < 5


def test_worker_deadletters_after_exhaustion(db_session, tenant, monkeypatch):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    src = ingestion_runs.get_or_create_source(
        db_session, tenant.id, coll.id, "s3_mock", "B3",
        config={"connector_type": "s3_mock", "objects": {"x.txt": "boom"}})
    run = ingestion_runs.create_queued_run(db_session, src)
    monkeypatch.setattr(ingestion_runs, "sync_source",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    q = RedisJobQueue(fakeredis.FakeStrictRedis(decode_responses=True))
    job = {"kind": "sync_source", "source_id": str(src.id),
           "run_id": str(run.id), "attempt": worker.MAX_ATTEMPTS - 1}
    worker.handle_job(db_session, job, emb, vs, None, q)
    db_session.refresh(run)
    assert run.status == "failed" and q.depth()["dead"] == 1


# ---- system status endpoint (admin) ----

def test_system_status_endpoint(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "st"}).json()["id"]
    api_client.post("/documents/upload", data={"tenant_id": tid, "collection_id": cid},
                    files={"file": ("p.txt", b"vacation policy twenty days " * 5, "text/plain")})
    r = api_client.get("/admin/system/status", params={"tenant_id": tid})
    assert r.status_code == 200
    body = r.json()
    assert "worker" in body and "queue" in body
    assert body["ingestion_total"] >= 1
    assert 0.0 <= body["success_rate"] <= 1.0
    assert isinstance(body["sources"], list)


def test_retry_only_failed_runs(api_client, tenant, db_session):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "rt"}).json()["id"]
    # a succeeded manual run cannot be retried
    up = api_client.post("/documents/upload", data={"tenant_id": tid, "collection_id": cid},
                         files={"file": ("p.txt", b"vacation " * 40, "text/plain")})
    assert up.status_code == 201
    runs = api_client.get("/ingestion/runs", params={"tenant_id": tid, "collection_id": cid}).json()
    rid = runs[0]["id"]
    r = api_client.post(f"/ingestion/runs/{rid}/retry", params={"tenant_id": tid})
    assert r.status_code == 400  # succeeded run -> not retryable
