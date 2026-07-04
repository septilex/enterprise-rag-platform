"""RET-08: query transformation improves recall vs the raw-query baseline."""

from app.core.config import settings
from app.db.models import Collection
from app.services import generation, ingestion
from app.services.query_transform import transform_query
from tests.fakes import FakeEmbedder, FakeLLM, InMemoryVectorStore


def test_transform_none_returns_original():
    assert transform_query("hi", FakeLLM(), "none") == "hi"


def test_rewrite_uses_llm_output():
    llm = FakeLLM(answer="vacation leave paid days off policy")
    assert transform_query("time off?", llm, "rewrite") == "vacation leave paid days off policy"


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="qt-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_rewrite_improves_recall(db_session, tenant, monkeypatch):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    # Target doc uses the word 'vacation'; the raw query does not.
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P",
        "Vacation vacation vacation entitlement is twenty days for staff.", emb, vs)

    # Baseline: raw query shares no terms -> no grounded context.
    monkeypatch.setattr(settings, "QUERY_TRANSFORM", "none")
    llm = FakeLLM(answer="Twenty days [1].")
    base = generation.prepare_grounding(
        db=db_session, query="paid leave", tenant_id=tenant.id,
        collection_id=coll.id, embedder=emb, vector_store=vs, llm=llm)
    assert base is None  # raw query misses the target

    # Rewrite injects the exact keyword -> retrieval now finds the doc.
    monkeypatch.setattr(settings, "QUERY_TRANSFORM", "rewrite")
    rewriter = FakeLLM(answer="vacation vacation entitlement staff")
    improved = generation.prepare_grounding(
        db=db_session, query="paid leave", tenant_id=tenant.id,
        collection_id=coll.id, embedder=emb, vector_store=vs, llm=rewriter)
    assert improved is not None  # recall improved (RET-08)
