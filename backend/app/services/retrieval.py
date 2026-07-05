"""Retrieval service: embed query, search Qdrant, hydrate chunks from Postgres."""

import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Chunk
from app.services.embedder import Embedder
from app.services.reranker import CrossEncoderReranker, get_reranker
import time as _time

from app.observability import record_cache, RETRIEVAL_LATENCY
from app.services.sparse import bm25_search
from app.services.vector_store import VectorStore
from app.tracing import span

_reranker = CrossEncoderReranker()


def _rrf_fuse(
    dense_ids: list[str],
    sparse_ids: list[str],
    k: int,
) -> list[str]:
    """Reciprocal-rank fusion of two ranked id lists -> fused id order (RET-01)."""
    scores: dict[str, float] = {}
    for ranked in (dense_ids, sparse_ids):
        for rank, cid in enumerate(ranked):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda cid: scores[cid], reverse=True)


def _serialize(results: list[dict]) -> list[dict]:
    """UUID-safe JSON form for the retrieval cache."""
    return [
        {**r, "chunk_id": str(r["chunk_id"]), "document_id": str(r["document_id"])}
        for r in results
    ]


def _deserialize(rows: list[dict]) -> list[dict]:
    return [
        {**r, "chunk_id": uuid.UUID(r["chunk_id"]),
         "document_id": uuid.UUID(r["document_id"])}
        for r in rows
    ]


def search_chunks(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    query: str,
    top_k: int,
    embedder: Embedder,
    vector_store: VectorStore,
    cache=None,
    no_cache: bool = False,
    metadata_filter: dict | None = None,
) -> list[dict]:
    """Embed query, search Qdrant scoped to tenant+collection, hydrate from Postgres.

    ``metadata_filter`` constrains results to chunks whose ingest-time metadata
    matches (ING-06 filtered retrieval / RET-04 metadata-ACL filtering).

    When a cache is supplied and ``no_cache`` is False, identical (tenant,
    collection, query, top_k, flags) lookups are served from Redis (CACHE-03),
    tenant-namespaced (CACHE-07) and bypassable per request (CACHE-08).
    """
    metadata_filter = metadata_filter or {}
    meta_conditions = {f"meta_{k}": v for k, v in metadata_filter.items()}

    flags = f"h{int(settings.HYBRID_ENABLED)}r{int(settings.RERANK_ENABLED)}"
    if meta_conditions:
        flags += "|" + ",".join(f"{k}={v}" for k, v in sorted(meta_conditions.items()))
    cache_key = None
    if cache is not None and not no_cache:
        cache_key = cache.retrieval_key(
            tenant_id, collection_id, query, top_k, flags
        )
        hit = cache.get_json(cache_key)
        if hit is not None:
            record_cache("retrieval", hit=True, saved=1)
            return _deserialize(hit)
        record_cache("retrieval", hit=False)

    _t0 = _time.perf_counter()

    # 1. Embed the query (single text → single vector)
    query_vector = embedder.embed([query])[0]

    # Over-fetch a larger candidate pool when reranking/hybrid; else just top_k.
    pool = (
        max(settings.RERANK_CANDIDATE_POOL, settings.SPARSE_CANDIDATE_POOL)
        if (settings.RERANK_ENABLED or settings.HYBRID_ENABLED)
        else top_k
    )

    # 2. Dense search: Qdrant with tenant + collection (+ metadata) filter
    with span("retrieval.dense"):
        qdrant_hits = vector_store.search(
            vector=query_vector,
            filters={
                "tenant_id": str(tenant_id),
                "collection_id": str(collection_id),
                **meta_conditions,
            },
            top_k=pool,
        )
    dense_ids = [h["id"] for h in qdrant_hits]
    score_map = {h["id"]: h["score"] for h in qdrant_hits}

    # 3. Optionally add sparse/keyword candidates and fuse (RET-01)
    if settings.HYBRID_ENABLED:
        with span("retrieval.sparse"):
            sparse = bm25_search(
                db, tenant_id, collection_id, query, settings.SPARSE_CANDIDATE_POOL
            )
        sparse_ids = [cid for cid, _ in sparse]
        ordered_ids = _rrf_fuse(dense_ids, sparse_ids, settings.RRF_K)
    else:
        ordered_ids = dense_ids

    if not ordered_ids:
        RETRIEVAL_LATENCY.observe(_time.perf_counter() - _t0)
        return []

    # 4. Hydrate chunk rows from Postgres, preserving fused order
    hit_uuids = [uuid.UUID(cid) for cid in ordered_ids]
    chunk_by_id = {
        str(c.id): c
        for c in db.query(Chunk).filter(Chunk.id.in_(hit_uuids)).all()
    }

    results: list[dict] = []
    for cid in ordered_ids:
        chunk = chunk_by_id.get(cid)
        if chunk is None:
            continue
        # Enforce metadata/ACL filter on the hydrated row too, so the sparse
        # (BM25) branch can never surface an out-of-scope chunk (RET-04).
        if meta_conditions and any(
            (chunk.doc_metadata or {}).get(k) != v
            for k, v in metadata_filter.items()
        ):
            continue
        results.append(
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                # Dense cosine score drives the confidence guard; sparse-only
                # hits (no dense score) default to 0.0.
                "score": score_map.get(cid, 0.0),
                "doc_metadata": chunk.doc_metadata,
            }
        )

    # 5. Rerank (modular stage: strategy selected by config, RET-03)
    reranker = get_reranker() if settings.RERANK_ENABLED else None
    if reranker is not None:
        with span("retrieval.rerank"):
            final = reranker.rerank(query, results, top_k)
    else:
        final = results[:top_k]

    if cache_key is not None:
        cache.set_json(cache_key, _serialize(final), settings.RETRIEVAL_CACHE_TTL)
    RETRIEVAL_LATENCY.observe(_time.perf_counter() - _t0)
    return final


def search_debug(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    query: str,
    embedder: Embedder,
    vector_store: VectorStore,
) -> dict:
    """DEBUG ONLY: expose dense candidate pool vs reranked order for RET-03 validation."""

    # 1. Embed query and fetch the full candidate pool from Qdrant
    query_vector = embedder.embed([query])[0]
    pool = settings.RERANK_CANDIDATE_POOL

    qdrant_hits = vector_store.search(
        vector=query_vector,
        filters={
            "tenant_id": str(tenant_id),
            "collection_id": str(collection_id),
        },
        top_k=pool,
    )

    if not qdrant_hits:
        return {"pool_size": 0, "dense_order": [], "reranked_order": []}

    # 2. Hydrate chunks from Postgres in dense-score order
    score_map = {h["id"]: h["score"] for h in qdrant_hits}
    hit_ids = [uuid.UUID(h["id"]) for h in qdrant_hits]
    rank = {h["id"]: i for i, h in enumerate(qdrant_hits)}

    chunk_rows = db.query(Chunk).filter(Chunk.id.in_(hit_ids)).all()
    chunk_rows.sort(key=lambda c: rank[str(c.id)])

    # Build candidates as list[(chunk_dict, dense_score)] — matches rerank_with_scores signature
    candidates: list[tuple[dict, float]] = [
        (
            {
                "chunk_id": c.id,
                "document_id": c.document_id,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "score": score_map[str(c.id)],
                "doc_metadata": c.doc_metadata,
            },
            score_map[str(c.id)],
        )
        for c in chunk_rows
    ]

    # 3. Rerank and collect scores
    reranked = _reranker.rerank_with_scores(query, candidates)

    def preview(text: str) -> str:
        return text[:80]

    return {
        "pool_size": len(candidates),
        "dense_order": [
            {
                "dense_rank": i,
                "dense_score": round(s, 4),
                "chunk_id": str(chunk["chunk_id"]),
                "preview": preview(chunk["content"]),
            }
            for i, (chunk, s) in enumerate(candidates)
        ],
        "reranked_order": [
            {
                "rerank_rank": i,
                "rerank_score": round(rs, 4),
                "dense_score": round(ds, 4),
                "chunk_id": str(chunk["chunk_id"]),
                "preview": preview(chunk["content"]),
            }
            for i, (chunk, ds, rs) in enumerate(reranked)
        ],
    }
