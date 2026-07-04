"""Embedder interface and OpenAI implementation."""

import abc

import openai

from app.core.config import settings


class Embedder(abc.ABC):
    """Abstract embedder — input texts, output float vectors."""

    @abc.abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAIEmbedder(Embedder):
    """OpenAI text-embedding-3-small embedder."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
    ):
        self.model = model
        self._client = openai.OpenAI(api_key=api_key or settings.OPENAI_API_KEY)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(input=texts, model=self.model)
        # sort by index to guarantee ordering matches input
        sorted_data = sorted(response.data, key=lambda d: d.index)
        return [d.embedding for d in sorted_data]


class CachedEmbedder(Embedder):
    """Wraps any Embedder with a content-hash embedding cache (CACHE-01).

    Only cache-missed texts are forwarded to the inner embedder, so repeated
    ingestion/query of identical text incurs no embedding API call.
    """

    def __init__(self, inner: Embedder, cache, model: str, ttl: int):
        self._inner = inner
        self._cache = cache
        self._model = model
        self._ttl = ttl

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float] | None] = [None] * len(texts)
        misses: list[int] = []

        from app.observability import record_cache

        for i, text in enumerate(texts):
            key = self._cache.embedding_key(self._model, text)
            cached = self._cache.get_json(key)
            if cached is not None:
                results[i] = cached
                record_cache("embedding", hit=True, saved=1)
            else:
                misses.append(i)
                record_cache("embedding", hit=False)

        if misses:
            fresh = self._inner.embed([texts[i] for i in misses])
            for idx, vec in zip(misses, fresh):
                results[idx] = vec
                self._cache.set_json(
                    self._cache.embedding_key(self._model, texts[idx]),
                    vec,
                    self._ttl,
                )

        return [r for r in results]  # type: ignore[return-value]
