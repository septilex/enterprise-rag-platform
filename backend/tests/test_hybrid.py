"""RET-01: hybrid retrieval surfaces keyword-exact matches dense-only misses."""

from app.core.config import settings
from app.db.models import Collection
from app.services import ingestion, retrieval
from app.services.retrieval import _rrf_fuse
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def test_rrf_fusion_orders_by_combined_rank():
    dense = ["a", "b", "c"]
    sparse = ["c", "d", "a"]
    fused = _rrf_fuse(dense, sparse, k=60)
    assert fused[0] in ("a", "c")          # appear in both lists -> top
    assert set(fused) == {"a", "b", "c", "d"}


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="hybrid-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_hybrid_surfaces_keyword_exact_match(db_session, tenant, monkeypatch):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()

    # Doc A: semantically about leave/time-off. Doc B: contains the exact rare
    # keyword 'XZ9000' that a bag-of-words dense embed won't associate with the
    # natural-language query.
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "A",
        "Employees may take paid time off and vacation days each calendar year.",
        emb, vs)
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "B",
        "The XZ9000 appliance requires firmware calibration before first use.",
        emb, vs)

    query = "XZ9000 firmware"

    monkeypatch.setattr(settings, "HYBRID_ENABLED", False)
    dense_only = retrieval.search_chunks(
        db=db_session, tenant_id=tenant.id, collection_id=coll.id,
        query=query, top_k=1, embedder=emb, vector_store=vs)

    monkeypatch.setattr(settings, "HYBRID_ENABLED", True)
    hybrid = retrieval.search_chunks(
        db=db_session, tenant_id=tenant.id, collection_id=coll.id,
        query=query, top_k=2, embedder=emb, vector_store=vs)

    hybrid_text = " ".join(h["content"] for h in hybrid)
    assert "XZ9000" in hybrid_text  # keyword match present in hybrid results
