"""Grounded answer generation using retrieved chunks + OpenAI chat."""

import uuid
from difflib import SequenceMatcher

import tiktoken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.embedder import Embedder
from app.services.llm import LLMClient
from app.services.retrieval import search_chunks
from app.services.vector_store import VectorStore


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
    """Keep adding chunks until the token budget is reached."""
    if token_budget <= 0:
        return []

    enc = tiktoken.get_encoding("cl100k_base")
    chosen: list[dict] = []
    used_tokens = 0

    for hit in hits:
        text = hit["content"]
        token_count = len(enc.encode(text))
        if used_tokens + token_count > token_budget:
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


def generate_answer(
    db: Session,
    query: str,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    embedder: Embedder,
    vector_store: VectorStore,
    llm: LLMClient,
    top_k: int | None = None,
) -> tuple[bool, str, list[dict]]:
    """
    Retrieve relevant chunks, apply confidence guard, assemble context,
    and return grounded answer + citations.

    Returns:
        (grounded, answer, citations)
    """
    k = top_k or settings.CHAT_TOP_K

    hits = search_chunks(
        db=db,
        tenant_id=tenant_id,
        collection_id=collection_id,
        query=query,
        top_k=k,
        embedder=embedder,
        vector_store=vector_store,
    )

    # Low-confidence / no-results guard
    if not hits:
        return False, settings.NO_ANSWER_MESSAGE, []

    strong_hits = [
        hit for hit in hits if float(hit["score"]) >= settings.MIN_RETRIEVAL_SCORE
    ]
    if not strong_hits:
        return False, settings.NO_ANSWER_MESSAGE, []

    # Deduplicate near-identical chunks
    deduped_hits = _dedup_hits(
        strong_hits, settings.DEDUP_SIMILARITY_THRESHOLD
    )

    # Keep only the single best chunk for now to avoid noisy citations
    top_hits = deduped_hits[:1]

    # Fit to token budget
    final_hits = _fit_hits_to_token_budget(
        top_hits, settings.CONTEXT_TOKEN_BUDGET
    )

    if not final_hits:
        return False, settings.NO_ANSWER_MESSAGE, []

    context_block = _build_context_block(final_hits)
    citations = _build_citations(final_hits)

    system_prompt = """You are a grounded RAG assistant.

Answer the user's question using ONLY the provided context.
Rules:
- If the answer is not supported by the context, say you do not have enough grounded information.
- Do not use outside knowledge.
- When you make a claim supported by the context, include inline citations like [1], [2].
- Only cite chunk numbers that exist in the provided context.
- Keep the answer concise but useful.
"""

    user_prompt = f"""Question:
{query}

Context:
{context_block}

Return a grounded answer using inline citation markers like [1] or [2].
"""

    answer = llm.complete(system=system_prompt, user=user_prompt).strip()

    if not answer:
        return False, settings.NO_ANSWER_MESSAGE, []

    # If model still refuses / gives nothing useful, keep contract safe
    lowered = answer.lower()
    fallback_signals = [
        "i don't have enough grounded information",
        "i do not have enough grounded information",
        "not enough grounded information",
        "not enough information in the provided context",
        "cannot answer based on the provided context",
        "can't answer based on the provided context",
    ]

    if any(signal in lowered for signal in fallback_signals):
        return False, settings.NO_ANSWER_MESSAGE, []

    return True, answer, citations