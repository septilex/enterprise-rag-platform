"""ING-06 / RET-04: metadata tagging + metadata/ACL-filtered retrieval."""

from app.db.models import Chunk, Collection
from app.services import ingestion, retrieval
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="meta-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_metadata_stored_on_chunks_and_payload(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P", "vacation policy twenty days",
        emb, vs, metadata={"author": "alice", "classification": "public"})

    chunk = db_session.query(Chunk).filter_by(collection_id=coll.id).first()
    assert chunk.doc_metadata["author"] == "alice"
    payload = next(iter(vs.points.values()))["payload"]
    assert payload["meta_author"] == "alice"


def test_metadata_filter_scopes_results(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Public", "vacation policy twenty days",
        emb, vs, metadata={"classification": "public"})
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Secret", "vacation policy twenty days secret",
        emb, vs, metadata={"classification": "restricted"})

    kw = dict(db=db_session, tenant_id=tenant.id, collection_id=coll.id,
              query="vacation policy", top_k=10, embedder=emb, vector_store=vs)

    public_only = retrieval.search_chunks(metadata_filter={"classification": "public"}, **kw)
    assert public_only
    assert all(h["doc_metadata"].get("classification") == "public" for h in public_only)

    # A restricted-scoped user sees zero public-only chunks and vice versa (RET-04).
    restricted = retrieval.search_chunks(
        metadata_filter={"classification": "restricted"}, **kw)
    assert all(h["doc_metadata"].get("classification") == "restricted" for h in restricted)
    assert set(h["chunk_id"] for h in public_only).isdisjoint(
        h["chunk_id"] for h in restricted)
