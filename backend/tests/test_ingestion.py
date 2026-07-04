"""ING-04 idempotency, ING-02 delta re-index, ING-08 delete propagation."""

from app.db.models import Chunk, Collection, Document
from app.services import ingestion
from tests.fakes import FakeEmbedder, InMemoryVectorStore

LONG = ("Vacation policy grants twenty days per year. " * 40)


def _collection(db, tenant):
    c = Collection(tenant_id=tenant.id, name="handbook")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_reingest_identical_is_noop(db_session, tenant):
    coll = _collection(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()

    doc1, n1, reused1 = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Policy", LONG, emb, vs)
    assert reused1 is False and n1 > 0
    vecs_after_first = len(vs.points)

    doc2, n2, reused2 = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Policy", LONG, emb, vs)

    assert reused2 is True
    assert doc2.id == doc1.id                      # same logical document
    assert n2 == n1                                # same chunk count reported
    # No net change in chunk/vector count (ING-04 acceptance criterion)
    assert db_session.query(Chunk).filter_by(document_id=doc1.id).count() == n1
    assert len(vs.points) == vecs_after_first


def test_content_change_reindexes_in_place(db_session, tenant):
    coll = _collection(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()

    doc1, n1, _ = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Policy", LONG, emb, vs)
    old_chunk_ids = {
        str(c.id) for c in db_session.query(Chunk).filter_by(document_id=doc1.id)
    }

    new_content = ("Sick leave is ten days per year. " * 60)
    doc2, n2, reused = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Policy", new_content, emb, vs)

    assert reused is False
    assert doc2.id == doc1.id                      # re-indexed in place
    new_chunk_ids = {
        str(c.id) for c in db_session.query(Chunk).filter_by(document_id=doc1.id)
    }
    assert old_chunk_ids.isdisjoint(new_chunk_ids)  # old chunks purged
    # Vector store holds exactly the new chunks, none of the old.
    assert set(vs.points.keys()) == new_chunk_ids
    assert old_chunk_ids.isdisjoint(set(vs.points.keys()))


def test_delete_document_removes_chunks_and_vectors(db_session, tenant):
    coll = _collection(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()

    doc, n, _ = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Policy", LONG, emb, vs)
    assert len(vs.points) == n

    ok = ingestion.delete_document(db_session, tenant.id, doc.id, vs)

    assert ok is True
    assert db_session.get(Document, doc.id) is None
    assert db_session.query(Chunk).filter_by(document_id=doc.id).count() == 0
    assert len(vs.points) == 0


def test_delete_document_is_tenant_scoped(db_session, tenant):
    coll = _collection(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    doc, _, _ = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Policy", LONG, emb, vs)

    import uuid
    other_tenant = uuid.uuid4()
    ok = ingestion.delete_document(db_session, other_tenant, doc.id, vs)

    assert ok is False                              # cannot delete across tenants
    assert db_session.get(Document, doc.id) is not None
