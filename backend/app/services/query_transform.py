"""Configurable pre-retrieval query transformation (RET-08).

Strategies rewrite the *search* query to improve recall while the original
user question is still what the LLM answers.

- ``rewrite``: expand/clarify the query with salient keywords.
- ``hyde``: generate a hypothetical answer passage and search with that
  (Hypothetical Document Embeddings).
"""

from __future__ import annotations

from app.services.llm import LLMClient
from app.tracing import span

_REWRITE_SYSTEM = (
    "You rewrite a user's question into a single, keyword-rich search query "
    "for a retrieval system. Preserve all entities and add obvious synonyms. "
    "Return only the rewritten query, no preamble."
)
_HYDE_SYSTEM = (
    "Write a short, factual passage that would directly answer the user's "
    "question, as if from a reference document. Return only the passage."
)


def transform_query(query: str, llm: LLMClient, strategy: str) -> str:
    """Return the text to embed/search for. Falls back to the raw query on
    unknown strategy or empty model output."""
    if strategy == "none" or not strategy:
        return query
    if strategy == "rewrite":
        system = _REWRITE_SYSTEM
    elif strategy == "hyde":
        system = _HYDE_SYSTEM
    else:
        return query

    with span(f"query_transform.{strategy}"):
        out = llm.complete(system=system, user=query).strip()
    return out or query
