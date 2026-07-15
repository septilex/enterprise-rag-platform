"""Embedder interface and OpenAI implementation."""

import abc
import time

import openai

from app.core.config import settings


class Embedder(abc.ABC):
    """Abstract embedder — input texts, output float vectors."""

    @abc.abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAIEmbedder(Embedder):
    """OpenAI text-embedding-3-small embedder."""

    # OpenAI caps embeddings requests at 2048 inputs / ~300k tokens; batching
    # keeps arbitrarily large documents (e.g. a 650-page PDF) under both limits.
    BATCH_SIZE = 128
    # A large document is many sequential requests — one flaky connection must
    # not fail the whole job, so each batch retries with backoff on transient
    # network/throttle errors before giving up.
    BATCH_ATTEMPTS = 4

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
    ):
        self.model = model
        # Fail a hung request in 60s (SDK default is 600s) so batch-level
        # retries kick in quickly; the SDK's own 2 retries handle blips.
        self._client = openai.OpenAI(
            api_key=api_key or settings.OPENAI_API_KEY,
            timeout=60.0, max_retries=2,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            vectors.extend(self._embed_batch(texts[start:start + self.BATCH_SIZE]))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(self.BATCH_ATTEMPTS):
            try:
                response = self._client.embeddings.create(
                    input=batch, model=self.model)
                # sort by index to guarantee ordering matches input
                sorted_data = sorted(response.data, key=lambda d: d.index)
                return [d.embedding for d in sorted_data]
            except (openai.APIConnectionError, openai.RateLimitError,
                    openai.InternalServerError) as exc:  # transient only
                last = exc
                if attempt + 1 < self.BATCH_ATTEMPTS:
                    time.sleep(min(2 ** attempt, 8))
        assert last is not None
        raise last


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
