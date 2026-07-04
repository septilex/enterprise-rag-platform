"""ING-07: bad documents are quarantined (not silently dropped) and visible."""

from app.db.models import Chunk, Collection
from app.services import ingestion
from app.services.ingestion import validate_content
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def test_validate_content_flags_bad_input():
    assert validate_content("t", "   ") == "empty_content"
    assert validate_content("t", "\x00\x01\x02\x03\x04\x05binary\x06\x07") == "non_text_content"
    assert validate_content("t", "normal readable text") is None


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="q-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_bad_document_is_quarantined_not_dropped(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    doc, n, reused = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "bad.bin", "\x00\x01\x02\x03 garbage \x04\x05\x06",
        emb, vs)

    assert doc.status == "quarantined"
    assert doc.doc_metadata["failure_reason"] == "non_text_content"
    assert n == 0
    assert db_session.query(Chunk).filter_by(document_id=doc.id).count() == 0
    assert len(vs.points) == 0

    quarantined = ingestion.list_documents(
        db_session, tenant.id, coll.id, status="quarantined")
    assert doc.id in {d.id for d in quarantined}


def test_quarantined_endpoint_lists_failures(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "qc"}).json()["id"]
    # empty content is rejected by request validation; use non-text via a valid-length payload
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "bad",
        "content": "\x00\x01\x02\x03\x04\x05\x06\x07\x08"})
    r = api_client.get("/documents", params={
        "tenant_id": tid, "collection_id": cid, "status": "quarantined"})
    assert r.status_code == 200
    assert any(d["status"] == "quarantined" for d in r.json())
