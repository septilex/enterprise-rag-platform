"""Grounded answer generation using retrieved chunks + OpenAI chat."""

import uuid
from difflib import SequenceMatcher

import tiktoken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.observability import record_cache
from app.services.embedder import Embedder
from app.services.llm import LLMClient
from app.services.query_transform import transform_query
from app.services.retrieval import search_chunks
from app.services.usage import record_usage, LLM_TOKENS
from app.services.vector_store import VectorStore
from app.tracing import span


def _dedup_hits(hits: list[dict], similarity_threshold: float) -> list[dict]:
    """Remove near-duplicate chunks based on content similarity."""
    kept: list[dict] = []
    for hit in hits:
        content = hit["content"].strip()
        is_duplicate = False
        for existing in kept:
            ratio = SequenceMatcher(
                None, content, existing["content"].strip()
            ).ratio()
            if ratio >= similarity_threshold:
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(hit)
    return kept


def _fit_hits_to_token_budget(hits: list[dict], token_budget: int) -> list[dict]:
    """Keep adding chunks until the token budget is reached.

    The single highest-ranked chunk is always included (truncated to the budget
    if it alone overflows) so a large-but-relevant top hit never degrades into a
    false "no grounded answer" (UI-09).
    """
    if token_budget <= 0 or not hits:
        return []

    enc = tiktoken.get_encoding("cl100k_base")
    chosen: list[dict] = []
    used_tokens = 0

    for hit in hits:
        text = hit["content"]
        tokens = enc.encode(text)
        token_count = len(tokens)

        if used_tokens + token_count > token_budget:
            # Guarantee at least the top hit survives, truncated to fit.
            if not chosen:
                truncated = dict(hit)
                truncated["content"] = enc.decode(tokens[:token_budget])
                chosen.append(truncated)
            break

        chosen.append(hit)
        used_tokens += token_count

    return chosen


def _build_context_block(hits: list[dict]) -> str:
    """Build numbered context block for the LLM prompt."""
    blocks: list[str] = []
    for i, hit in enumerate(hits, start=1):
        blocks.append(
            f"[{i}] chunk_id={hit['chunk_id']}\n{hit['content']}"
        )
    return "\n\n".join(blocks)


def _build_citations(hits: list[dict]) -> list[dict]:
    """Build structured citations payload matching the numbered context."""
    citations: list[dict] = []
    for i, hit in enumerate(hits, start=1):
        citations.append(
            {
                "index": i,
                "chunk_id": hit["chunk_id"],
                "document_id": hit["document_id"],
                "chunk_index": hit["chunk_index"],
                "snippet": hit["content"][:300],
            }
        )
    return citations


_SYSTEM_PROMPT = """You are an enterprise document-grounded answering assistant.

You answer ONLY from the numbered context passages provided below, which are
retrieved from the user's selected document collection.

Strict rules:
- Use ONLY facts stated in the provided context. Never rely on prior knowledge,
  general world knowledge, or assumptions not present in the context.
- If the context does not contain enough information to answer, reply with
  EXACTLY this sentence and nothing else:
  "I don't have enough grounded information in the selected sources to answer that."
- Do not guess, extrapolate, or fabricate details, numbers, names, or citations.
- Every factual claim must include an inline citation like [1] or [2] referring
  to the numbered context passage it came from. Only cite passage numbers that
  actually appear in the context.
- Be concise and factual. Do not add disclaimers beyond the refusal sentence above.
"""

_FALLBACK_SIGNALS = [
    "i don't have enough grounded information",
    "i do not have enough grounded information",
    "not enough grounded information",
    "not enough information in the provided context",
    "cannot answer based on the provided context",
    "can't answer based on the provided context",
]


def _is_fallback(answer: str) -> bool:
    lowered = answer.lower()
    return any(signal in lowered for signal in _FALLBACK_SIGNALS)


_HOP_SYSTEM = (
    "You are helping a retrieval system answer a complex question that may need "
    "evidence from multiple documents. Given the question and what has been "
    "retrieved so far, produce ONE short follow-up search query for the missing "
    "piece of information. If nothing more is needed, reply with 'DONE'. Return "
    "only the query or 'DONE'."
)


def gather_multihop_hits(
    db,
    query: str,
    tenant_id,
    collection_id,
    embedder,
    vector_store,
    llm,
    k: int,
    cache,
    no_cache: bool,
    metadata_filter,
    max_hops: int,
) -> list[dict]:
    """Iterative retrieve→reason→retrieve, accumulating distinct chunks (RET-09)."""
    seen: set = set()
    collected: list[dict] = []
    hop_query = query

    for hop in range(max_hops):
        with span(f"retrieval.hop.{hop}"):
            hits = search_chunks(
                db=db, tenant_id=tenant_id, collection_id=collection_id,
                query=hop_query, top_k=k, embedder=embedder,
                vector_store=vector_store, cache=cache, no_cache=no_cache,
                metadata_filter=metadata_filter,
            )
        for h in hits:
            cid = str(h["chunk_id"])
            if cid not in seen:
                seen.add(cid)
                collected.append(h)

        if hop == max_hops - 1:
            break
        context = _build_context_block(collected)
        followup = llm.complete(
            system=_HOP_SYSTEM,
            user=f"Question:\n{query}\n\nRetrieved so far:\n{context}\n\nFollow-up query:",
        ).strip()
        if not followup or followup.upper().startswith("DONE"):
            break
        hop_query = followup

    return collected


def prepare_grounding(
    db: Session,
    query: str,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    embedder: Embedder,
    vector_store: VectorStore,
    top_k: int | None = None,
    cache=None,
    no_cache: bool = False,
    llm: LLMClient | None = None,
    metadata_filter: dict | None = None,
) -> tuple[list[dict], list[dict], str] | None:
    """Retrieve, guard, dedup, and assemble context for a query.

    Returns (final_hits, citations, user_prompt) or None when there is no
    grounded context (no hits / below confidence threshold) — the UI-09 guard.
    Shared by both the blocking and streaming chat paths.
    """
    k = top_k or settings.CHAT_TOP_K

    # Optional pre-retrieval query transformation (RET-08). The original
    # question is preserved for the answer prompt; only the search text changes.
    search_query = query
    if llm is not None and settings.QUERY_TRANSFORM != "none":
        search_query = transform_query(query, llm, settings.QUERY_TRANSFORM)

    if settings.MULTI_HOP_ENABLED and llm is not None:
        # Agentic multi-hop: iteratively retrieve, reason for a follow-up
        # sub-query, and retrieve again, combining evidence (RET-09).
        hits = gather_multihop_hits(
            db, search_query, tenant_id, collection_id, embedder, vector_store,
            llm, k, cache, no_cache, metadata_filter, settings.MULTI_HOP_MAX_HOPS,
        )
    else:
        hits = search_chunks(
            db=db, tenant_id=tenant_id, collection_id=collection_id, query=search_query,
            top_k=k, embedder=embedder, vector_store=vector_store,
            cache=cache, no_cache=no_cache, metadata_filter=metadata_filter,
        )
    if not hits:
        return None

    strong_hits = [
        hit for hit in hits if float(hit["score"]) >= settings.MIN_RETRIEVAL_SCORE
    ]
    if not strong_hits:
        return None

    deduped_hits = _dedup_hits(strong_hits, settings.DEDUP_SIMILARITY_THRESHOLD)
    capped_hits = deduped_hits[: settings.CHAT_MAX_CONTEXT_CHUNKS]
    final_hits = _fit_hits_to_token_budget(capped_hits, settings.CONTEXT_TOKEN_BUDGET)
    if not final_hits:
        return None

    context_block = _build_context_block(final_hits)
    citations = _build_citations(final_hits)
    user_prompt = (
        f"Question:\n{query}\n\nContext:\n{context_block}\n\n"
        "Return a grounded answer using inline citation markers like [1] or [2].\n"
    )
    return final_hits, citations, user_prompt


def generate_answer(
    db: Session,
    query: str,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    embedder: Embedder,
    vector_store: VectorStore,
    llm: LLMClient,
    top_k: int | None = None,
    cache=None,
    no_cache: bool = False,
    metadata_filter: dict | None = None,
) -> tuple[bool, str, list[dict]]:
    """Blocking grounded answer. Returns (grounded, answer, citations)."""
    # Semantic response cache: a paraphrase of a prior query above the
    # similarity threshold returns the cached answer without a fresh LLM call
    # (CACHE-02). Bypassable per request (CACHE-08).
    use_semantic = (
        cache is not None and settings.SEMANTIC_CACHE_ENABLED and not no_cache
    )
    query_vec = None
    if use_semantic:
        query_vec = embedder.embed([query])[0]
        cached = cache.semantic_lookup(
            tenant_id, collection_id, query_vec,
            settings.SEMANTIC_CACHE_THRESHOLD, settings.SEMANTIC_CACHE_MAX_ENTRIES,
        )
        if cached is not None:
            record_cache("semantic", hit=True, saved=1)  # avoided an LLM call
            return cached["grounded"], cached["answer"], _uuidify_citations(
                cached["citations"]
            )
        record_cache("semantic", hit=False)

    prepared = prepare_grounding(
        db, query, tenant_id, collection_id, embedder, vector_store,
        top_k=top_k, cache=cache, no_cache=no_cache, llm=llm,
        metadata_filter=metadata_filter,
    )
    if prepared is None:
        return False, settings.NO_ANSWER_MESSAGE, []

    _final_hits, citations, user_prompt = prepared
    with span("generation.llm"):
        answer = llm.complete(system=_SYSTEM_PROMPT, user=user_prompt).strip()

    # Attribute token usage for chargeback (MON-08).
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = len(enc.encode(_SYSTEM_PROMPT + user_prompt + answer))
    record_usage(db, tenant_id, collection_id, LLM_TOKENS, tokens)

    if not answer or _is_fallback(answer):
        return False, settings.NO_ANSWER_MESSAGE, []

    if use_semantic:
        cache.semantic_store(
            tenant_id, collection_id, query_vec,
            {
                "grounded": True,
                "answer": answer,
                "citations": _jsonable_citations(citations),
            },
            settings.SEMANTIC_CACHE_TTL, settings.SEMANTIC_CACHE_MAX_ENTRIES,
        )

    return True, answer, citations


def _uuidify_citations(citations: list[dict]) -> list[dict]:
    """Rehydrate UUID fields on citations restored from the JSON cache."""
    return [
        {**c, "chunk_id": uuid.UUID(c["chunk_id"]),
         "document_id": uuid.UUID(c["document_id"])}
        for c in citations
    ]


def stream_answer(
    db: Session,
    query: str,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    embedder: Embedder,
    vector_store: VectorStore,
    llm: LLMClient,
    top_k: int | None = None,
    cache=None,
    no_cache: bool = False,
    metadata_filter: dict | None = None,
):
    """Yield SSE-shaped events for streaming chat (UI-01/06).

    Event order: one ``citations`` event, then N ``token`` events, then a
    terminal ``done`` event carrying the grounded flag. When there is no
    grounded context, emits a single ``done`` event with grounded=False and
    the no-answer message (UI-09).
    """
    prepared = prepare_grounding(
        db, query, tenant_id, collection_id, embedder, vector_store,
        top_k=top_k, cache=cache, no_cache=no_cache, llm=llm,
        metadata_filter=metadata_filter,
    )
    if prepared is None:
        yield {"type": "done", "grounded": False, "message": settings.NO_ANSWER_MESSAGE}
        return

    _final_hits, citations, user_prompt = prepared
    # Citations up front so the UI can render sources alongside the stream (UI-02).
    yield {"type": "citations", "citations": _jsonable_citations(citations)}

    collected: list[str] = []
    for token in llm.stream(system=_SYSTEM_PROMPT, user=user_prompt):
        collected.append(token)
        yield {"type": "token", "text": token}

    full = "".join(collected).strip()
    grounded = bool(full) and not _is_fallback(full)
    yield {"type": "done", "grounded": grounded}


def _jsonable_citations(citations: list[dict]) -> list[dict]:
    return [
        {**c, "chunk_id": str(c["chunk_id"]), "document_id": str(c["document_id"])}
        for c in citations
    ]