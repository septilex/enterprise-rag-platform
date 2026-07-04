"""RAG quality evaluation scorecard (MON-03).

Computes retrieval precision/recall against a labeled set plus answer
groundedness / hallucination rate. Runnable as a scheduled job or via the
/eval/scorecard endpoint to produce a quality scorecard on a cadence.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.services.embedder import Embedder
from app.services.generation import generate_answer
from app.services.llm import LLMClient
from app.services.retrieval import search_chunks
from app.services.vector_store import VectorStore


def precision_recall_at_k(
    retrieved_ids: list[str], relevant_ids: set[str], k: int
) -> tuple[float, float]:
    """Precision@k and recall@k for one query."""
    topk = retrieved_ids[:k]
    if not topk:
        return 0.0, 0.0 if relevant_ids else 1.0
    hits = sum(1 for cid in topk if cid in relevant_ids)
    precision = hits / len(topk)
    recall = hits / len(relevant_ids) if relevant_ids else 1.0
    return precision, recall


def citations_grounded(citation_chunk_ids: list[str], retrieved_ids: set[str]) -> bool:
    """An answer is grounded when every cited chunk was actually retrieved."""
    return all(cid in retrieved_ids for cid in citation_chunk_ids)


def run_scorecard(
    db: Session,
    eval_set: list[dict],
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    embedder: Embedder,
    vector_store: VectorStore,
    llm: LLMClient,
    k: int = 5,
) -> dict:
    """Score an eval set of {query, relevant_chunk_ids}.

    Returns a scorecard with mean retrieval precision/recall@k, groundedness
    rate, and hallucination rate.
    """
    precisions: list[float] = []
    recalls: list[float] = []
    grounded_flags: list[bool] = []

    for example in eval_set:
        query = example["query"]
        relevant = {str(c) for c in example.get("relevant_chunk_ids", [])}

        hits = search_chunks(
            db=db, tenant_id=tenant_id, collection_id=collection_id, query=query,
            top_k=k, embedder=embedder, vector_store=vector_store, no_cache=True,
        )
        retrieved_ids = [str(h["chunk_id"]) for h in hits]
        p, r = precision_recall_at_k(retrieved_ids, relevant, k)
        precisions.append(p)
        recalls.append(r)

        grounded, _answer, citations = generate_answer(
            db=db, query=query, tenant_id=tenant_id, collection_id=collection_id,
            embedder=embedder, vector_store=vector_store, llm=llm, no_cache=True,
        )
        cited = [str(c["chunk_id"]) for c in citations]
        grounded_flags.append(
            grounded and citations_grounded(cited, set(retrieved_ids))
        )

    n = len(eval_set) or 1
    grounded_rate = sum(1 for g in grounded_flags if g) / n
    return {
        "n": len(eval_set),
        "k": k,
        "precision_at_k": round(sum(precisions) / n, 4),
        "recall_at_k": round(sum(recalls) / n, 4),
        "groundedness": round(grounded_rate, 4),
        "hallucination_rate": round(1.0 - grounded_rate, 4),
    }
