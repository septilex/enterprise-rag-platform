"""ING-09: bulk backfill lane never blocks incremental updates."""

import fakeredis

from app.db.models import Collection, Document
from app.services import jobs
from app.services.jobs import RedisJobQueue
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="jobs-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_incremental_drains_before_bulk():
    q = RedisJobQueue(fakeredis.FakeStrictRedis(decode_responses=True))
    q.enqueue({"id": "bulk1"}, bulk=True)
    q.enqueue({"id": "bulk2"}, bulk=True)
    q.enqueue({"id": "incr1"}, bulk=False)  # enqueued last...

    # ...but drained first (priority lane), so backfill can't starve it (ING-09).
    assert q.dequeue()["id"] == "incr1"
    assert q.dequeue()["id"] in {"bulk1", "bulk2"}
    assert q.dequeue()["id"] in {"bulk1", "bulk2"}
    assert q.dequeue() is None


def test_process_next_ingests_job(db_session, tenant):
    coll = _coll(db_session, tenant)
    q = RedisJobQueue(fakeredis.FakeStrictRedis(decode_responses=True))
    q.enqueue({
        "tenant_id": str(tenant.id), "collection_id": str(coll.id),
        "title": "Backfilled", "content": "vacation policy twenty days",
    }, bulk=True)

    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    assert jobs.process_next(db_session, q, emb, vs) is True
    assert jobs.process_next(db_session, q, emb, vs) is False  # queue drained

    assert db_session.query(Document).filter_by(collection_id=coll.id).count() == 1


def test_depth_reports_both_lanes():
    q = RedisJobQueue(fakeredis.FakeStrictRedis(decode_responses=True))
    q.enqueue({"id": 1}, bulk=True)
    q.enqueue({"id": 2}, bulk=False)
    assert q.depth() == {"incremental": 1, "bulk": 1}
