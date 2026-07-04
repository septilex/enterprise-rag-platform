"""Sparse/keyword retrieval over Postgres full-text search (RET-01).

Complements dense vector search with exact-term (BM25-style) matching so
keyword-precise queries are not missed by embeddings alone.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

_SPARSE_SQL = text(
    """
    SELECT id,
           ts_rank(to_tsvector('english', content),
                   websearch_to_tsquery('english', :q)) AS rank
    FROM chunks
    WHERE tenant_id = :tenant_id
      AND collection_id = :collection_id
      AND to_tsvector('english', content) @@ websearch_to_tsquery('english', :q)
    ORDER BY rank DESC
    LIMIT :pool
    """
)


def bm25_search(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    query: str,
    pool: int,
) -> list[tuple[str, float]]:
    """Return [(chunk_id_str, rank_score)] ordered best-first. Empty on blank query."""
    if not query.strip():
        return []
    rows = db.execute(
        _SPARSE_SQL,
        {
            "q": query,
            "tenant_id": str(tenant_id),
            "collection_id": str(collection_id),
            "pool": pool,
        },
    ).all()
    return [(str(row.id), float(row.rank)) for row in rows]
