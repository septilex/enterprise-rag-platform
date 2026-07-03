"""Retrieval service: embed query, search Qdrant, hydrate chunks from Postgres."""

import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Chunk
from app.services.embedder import Embedder
from app.services.reranker import CrossEncoderReranker
from app.services.vector_store import VectorStore

_reranker = CrossEncoderReranker()


def search_chunks(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    query: str,
    top_k: int,
    embedder: Embedder,
    vector_store: VectorStore,
) -> list[dict]:
    """Embed query, search Qdrant scoped to tenant+collection, hydrate from Postgres."""

    # 1. Embed the query (single text → single vector)
    query_vector = embedder.embed([query])[0]

    # Over-fetch a larger candidate pool when reranking; otherwise just top_k.
    pool = settings.RERANK_CANDIDATE_POOL if settings.RERANK_ENABLED else top_k

    # 2. Search Qdrant with tenant + collection filter
    #    Payload fields are stored as strings (see ingestion.py)
    qdrant_hits = vector_store.search(
        vector=query_vector,
        filters={
            "tenant_id": str(tenant_id),
            "collection_id": str(collection_id),
        },
        top_k=pool,
    )

    if not qdrant_hits:
        return []

    # 3. Hydrate chunk rows from Postgres (preserving Qdrant score order)
    hit_ids = [uuid.UUID(h["id"]) for h in qdrant_hits]
    score_map = {h["id"]: h["score"] for h in qdrant_hits}

    chunk_rows = db.query(Chunk).filter(Chunk.id.in_(hit_ids)).all()
    chunk_by_id = {str(c.id): c for c in chunk_rows}

    # 4. Build results in score-ranked order
    results: list[dict] = []
    for hit in qdrant_hits:
        chunk = chunk_by_id.get(hit["id"])
        if chunk is None:
            continue
        results.append(
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "score": score_map[hit["id"]],
                "doc_metadata": chunk.doc_metadata,
            }
        )

    # 5. Rerank
    if settings.RERANK_ENABLED:
        return _reranker.rerank(query, results, top_k)
    return results[:top_k]
