"""RET-09: multi-hop retrieval combines evidence a single pass would miss."""

from app.db.models import Collection
from app.services import generation, ingestion
from tests.fakes import FakeEmbedder, FakeLLM, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="hop-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_multihop_gathers_second_document(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    # Doc A answers hop 1 (term 'zeus'); Doc B holds the linked fact (term 'omega').
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "A", "alpha alpha manager zeus leads", emb, vs)
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "B", "omega omega budget five million", emb, vs)

    # Single hop on 'zeus' (top_k=1) finds only Doc A.
    single = generation.search_chunks(
        db=db_session, tenant_id=tenant.id, collection_id=coll.id,
        query="zeus", top_k=1, embedder=emb, vector_store=vs)
    single_docs = {h["document_id"] for h in single}
    assert len(single_docs) == 1

    # Multi-hop: the LLM's follow-up query surfaces Doc B via 'omega budget'.
    llm = FakeLLM(answer="omega budget")
    combined = generation.gather_multihop_hits(
        db_session, "zeus", tenant.id, coll.id, emb, vs, llm,
        k=1, cache=None, no_cache=True, metadata_filter=None, max_hops=2)
    combined_docs = {h["document_id"] for h in combined}
    assert len(combined_docs) == 2  # both documents combined (RET-09)
    assert single_docs.issubset(combined_docs)
