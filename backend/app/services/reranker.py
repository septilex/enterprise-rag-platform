import re
from abc import ABC, abstractmethod
from collections import Counter

from app.core.config import settings

_WORD_RE = re.compile(r"[a-z0-9]+")


class Reranker(ABC):
    """Swappable reranking interface (RET-03)."""

    @abstractmethod
    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """candidates: list[dict] -> reordered top_k list[dict]."""
        ...


class HeuristicReranker(Reranker):
    """Lexical cross-scoring reranker — no torch, safe for the light image.

    Blends the dense retrieval score with query-term coverage + BM25-ish term
    saturation over each candidate, promoting chunks that actually contain the
    query terms. A pragmatic RET-03 default when a cross-encoder GPU model is
    not deployed.
    """

    def __init__(self, k1: float = 1.5, dense_weight: float = 0.3):
        self.k1 = k1
        self.dense_weight = dense_weight

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _WORD_RE.findall(text.lower())

    def _score(self, q_terms: set[str], content: str, dense: float) -> float:
        counts = Counter(self._tokens(content))
        length = sum(counts.values()) or 1
        lexical = 0.0
        for term in q_terms:
            tf = counts.get(term, 0)
            if tf:
                # BM25-style term saturation, length-normalized.
                lexical += (tf * (self.k1 + 1)) / (tf + self.k1 * (length / 64.0))
        coverage = sum(1 for t in q_terms if counts.get(t, 0)) / (len(q_terms) or 1)
        return lexical + coverage + self.dense_weight * float(dense or 0.0)

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if not candidates:
            return []
        q_terms = set(self._tokens(query))
        scored = sorted(
            candidates,
            key=lambda c: self._score(q_terms, c["content"], c.get("score", 0.0)),
            reverse=True,
        )
        return scored[:top_k]


class CrossEncoderReranker(Reranker):
    def __init__(self):
        self._model = None  # lazy: not loaded until first rerank call (keeps reloads fast)

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:  # optional heavy dep, see requirements-rerank.txt
                raise RuntimeError(
                    "RERANK_ENABLED is true but 'sentence-transformers' is not "
                    "installed. Install requirements-rerank.txt (pulls torch) or "
                    "set RERANK_ENABLED=false."
                ) from exc
            self._model = CrossEncoder(settings.RERANK_MODEL)
        return self._model

    def rerank_with_scores(self, query: str, candidates: list) -> list:
        """DEBUG ONLY: return [(chunk_dict, dense_score, rerank_score)] sorted by rerank_score desc."""
        if not candidates:
            return []
        model = self._get_model()
        pairs = [(query, chunk["content"]) for chunk, _ in candidates]
        scores = model.predict(pairs)
        scored = [
            (chunk, dense_score, float(rerank_score))
            for (chunk, dense_score), rerank_score in zip(candidates, scores)
        ]
        scored.sort(key=lambda item: item[2], reverse=True)
        return scored

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if not candidates:
            return []
        model = self._get_model()
        pairs = [(query, cand["content"]) for cand in candidates]
        scores = model.predict(pairs)
        ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
        # Preserve the original dict; only the ORDER changes.
        return [cand for cand, _rerank_score in ranked[:top_k]]


# Cache reranker instances so lazy models load once.
_INSTANCES: dict[str, Reranker] = {}


def get_reranker(strategy: str | None = None) -> Reranker | None:
    """Return the configured reranker, or None when reranking is off.

    strategy: "none" | "heuristic" | "cross_encoder". Falls back to
    settings.RERANK_STRATEGY. Keeps retrieval modular: retrieve -> rerank ->
    assemble, with the rerank stage swappable by config (RET-03).
    """
    strategy = strategy or settings.RERANK_STRATEGY
    if strategy in ("none", ""):
        return None
    if strategy not in _INSTANCES:
        if strategy == "heuristic":
            _INSTANCES[strategy] = HeuristicReranker()
        elif strategy == "cross_encoder":
            _INSTANCES[strategy] = CrossEncoderReranker()
        else:
            raise ValueError(f"unknown rerank strategy: {strategy}")
    return _INSTANCES[strategy]
