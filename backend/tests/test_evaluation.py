"""MON-03: RAG quality scorecard (precision/recall + groundedness)."""

from app.db.models import Chunk, Collection
from app.services import evaluation, ingestion
from tests.fakes import FakeEmbedder, FakeLLM, InMemoryVectorStore


def test_precision_recall_at_k():
    p, r = evaluation.precision_recall_at_k(["a", "b", "c"], {"a", "c"}, k=3)
    assert round(p, 3) == round(2 / 3, 3)
    assert r == 1.0


def test_citations_grounded():
    assert evaluation.citations_grounded(["a", "b"], {"a", "b", "c"}) is True
    assert evaluation.citations_grounded(["z"], {"a"}) is False


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="eval-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_scorecard_runs_over_labeled_set(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P",
        "Vacation policy grants twenty paid vacation days per year.", emb, vs)

    # Label the ingested chunk(s) as relevant for a matching query.
    chunk_ids = [str(c.id) for c in db_session.query(Chunk).filter_by(
        collection_id=coll.id)]

    scorecard = evaluation.run_scorecard(
        db=db_session,
        eval_set=[{"query": "vacation days", "relevant_chunk_ids": chunk_ids}],
        tenant_id=tenant.id, collection_id=coll.id,
        embedder=emb, vector_store=vs, llm=FakeLLM(answer="Twenty [1]."), k=5)

    assert scorecard["n"] == 1
    assert scorecard["recall_at_k"] == 1.0          # relevant chunk retrieved
    assert 0.0 <= scorecard["precision_at_k"] <= 1.0
    assert scorecard["groundedness"] + scorecard["hallucination_rate"] == 1.0
