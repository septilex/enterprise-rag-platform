"""SEC-06: right-to-erasure removes document, chunks, vectors, and feedback refs."""

from app.db.models import Chunk, Collection, Document, Feedback
from app.services import ingestion
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="erase-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_erasure_purges_all_references(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    doc, _, _ = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P", "vacation policy twenty days", emb, vs)

    chunk_ids = [str(c.id) for c in db_session.query(Chunk).filter_by(document_id=doc.id)]
    db_session.add(Feedback(
        tenant_id=tenant.id, collection_id=coll.id, query="q", answer="a",
        rating="down", chunk_ids=chunk_ids))
    db_session.commit()

    result = ingestion.erase_document(db_session, tenant.id, doc.id, vs)

    assert result["chunks_erased"] == len(chunk_ids)
    assert result["feedback_erased"] == 1
    assert db_session.get(Document, doc.id) is None
    assert db_session.query(Chunk).filter_by(document_id=doc.id).count() == 0
    assert len(vs.points) == 0
    assert db_session.query(Feedback).filter_by(tenant_id=tenant.id).count() == 0


def test_erase_unknown_returns_404(api_client, tenant):
    import uuid
    r = api_client.post(f"/documents/{uuid.uuid4()}/erase", params={"tenant_id": str(tenant.id)})
    assert r.status_code == 404
