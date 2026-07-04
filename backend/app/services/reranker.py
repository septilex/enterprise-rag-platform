from abc import ABC, abstractmethod

from app.core.config import settings


class Reranker(ABC):
    """Swappable reranking interface (RET-03)."""

    @abstractmethod
    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """candidates: list[dict] -> reordered top_k list[dict]."""
        ...


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
