"""Shared Redis cache (CACHE-01/03/04/05/07/08).

- Redis-backed so every API/retrieval replica shares hits (CACHE-05).
- Keys are tenant-namespaced for content-bearing caches (CACHE-07).
- TTLs + prefix invalidation tie cache lifetime to ingestion events (CACHE-04).
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid

from app.core.config import settings


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class RedisCache:
    """Thin JSON wrapper over a redis client (real or fakeredis in tests)."""

    def __init__(self, client):
        self._r = client

    def get_json(self, key: str):
        raw = self._r.get(key)
        return json.loads(raw) if raw is not None else None

    def set_json(self, key: str, value, ttl: int) -> None:
        self._r.set(key, json.dumps(value), ex=ttl)

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for k in self._r.scan_iter(match=f"{prefix}*"):
            self._r.delete(k)
            n += 1
        return n

    # --- key builders -----------------------------------------------------
    @staticmethod
    def embedding_key(model: str, text: str) -> str:
        # Content-hash keyed and NOT tenant-scoped: an embedding is a pure
        # function of (model, text); a cross-tenant hit reveals nothing the
        # caller does not already hold. Satisfies CACHE-01 dedup safely.
        return f"emb:{model}:{_sha(text)}"

    @staticmethod
    def retrieval_prefix(tenant_id: uuid.UUID, collection_id: uuid.UUID) -> str:
        return f"retr:{tenant_id}:{collection_id}:"

    @classmethod
    def retrieval_key(
        cls, tenant_id, collection_id, query: str, top_k: int, flags: str
    ) -> str:
        digest = _sha(f"{query}|{top_k}|{flags}")
        return f"{cls.retrieval_prefix(tenant_id, collection_id)}{digest}"

    # --- semantic response cache (CACHE-02) --------------------------------
    @staticmethod
    def semantic_prefix(tenant_id, collection_id) -> str:
        return f"sem:{tenant_id}:{collection_id}"

    def semantic_lookup(
        self, tenant_id, collection_id, query_vec, threshold, max_entries
    ):
        """Return a cached payload whose stored query is >= threshold similar.

        Tenant+collection scoped (CACHE-07); linear scan of the most recent
        entries bounded by max_entries.
        """
        key = self.semantic_prefix(tenant_id, collection_id)
        best, best_payload = -1.0, None
        for raw in self._r.lrange(key, 0, max_entries - 1):
            entry = json.loads(raw)
            sim = _cosine(query_vec, entry["v"])
            if sim > best:
                best, best_payload = sim, entry["p"]
        return best_payload if best >= threshold else None

    def semantic_store(
        self, tenant_id, collection_id, query_vec, payload, ttl, max_entries
    ) -> None:
        key = self.semantic_prefix(tenant_id, collection_id)
        self._r.lpush(key, json.dumps({"v": query_vec, "p": payload}))
        self._r.ltrim(key, 0, max_entries - 1)
        self._r.expire(key, ttl)


def build_cache() -> RedisCache | None:
    """Construct the shared cache from settings, or None when disabled."""
    if not settings.CACHE_ENABLED:
        return None
    import redis

    return RedisCache(redis.Redis.from_url(settings.REDIS_URL, decode_responses=True))
