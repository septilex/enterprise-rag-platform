"""CACHE-01/03/04/07/08: embedding cache, retrieval cache, isolation, bypass."""

import uuid

import fakeredis

from app.core.config import settings
from app.db.models import Collection
from app.services import ingestion, retrieval
from app.services.cache import RedisCache
from app.services.embedder import CachedEmbedder
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _cache():
    return RedisCache(fakeredis.FakeStrictRedis(decode_responses=True))


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="cache-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_embedding_cache_avoids_repeat_calls():
    inner = FakeEmbedder()
    cached = CachedEmbedder(inner, _cache(), "m", ttl=60)

    v1 = cached.embed(["hello world"])
    v2 = cached.embed(["hello world"])

    assert v1 == v2
    assert len(inner.calls) == 1  # second call served from cache (CACHE-01)


def test_retrieval_cache_hit_skips_vector_store(db_session, tenant, monkeypatch):
    monkeypatch.setattr(settings, "HYBRID_ENABLED", False)
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    coll = _coll(db_session, tenant)
    emb, vs, cache = FakeEmbedder(), InMemoryVectorStore(), _cache()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Doc",
        "vacation policy grants twenty days", emb, vs)

    kw = dict(db=db_session, tenant_id=tenant.id, collection_id=coll.id,
              query="vacation", top_k=3, embedder=emb, vector_store=vs, cache=cache)

    first = retrieval.search_chunks(**kw)
    calls_after_first = len(vs.search_calls) if hasattr(vs, "search_calls") else None

    # Break the store; a cache hit must not touch it (CACHE-03).
    monkeypatch.setattr(vs, "search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("vector store hit")))
    second = retrieval.search_chunks(**kw)

    assert [h["chunk_id"] for h in first] == [h["chunk_id"] for h in second]


def test_no_cache_bypass_forces_fresh_path(db_session, tenant, monkeypatch):
    monkeypatch.setattr(settings, "HYBRID_ENABLED", False)
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    coll = _coll(db_session, tenant)
    emb, vs, cache = FakeEmbedder(), InMemoryVectorStore(), _cache()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Doc", "vacation policy", emb, vs)

    kw = dict(db=db_session, tenant_id=tenant.id, collection_id=coll.id,
              query="vacation", top_k=3, embedder=emb, vector_store=vs, cache=cache)
    retrieval.search_chunks(**kw)  # populate cache

    hit_count = {"n": 0}
    real_search = vs.search

    def counting(*a, **k):
        hit_count["n"] += 1
        return real_search(*a, **k)

    monkeypatch.setattr(vs, "search", counting)
    retrieval.search_chunks(no_cache=True, **kw)  # CACHE-08

    assert hit_count["n"] == 1  # bypass reran the real search


def test_cache_is_tenant_isolated():
    cache = _cache()
    t1, t2, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    k1 = cache.retrieval_key(t1, c, "q", 3, "flags")
    k2 = cache.retrieval_key(t2, c, "q", 3, "flags")
    assert k1 != k2  # same query, different tenant -> different key (CACHE-07)


def test_invalidation_clears_collection_prefix(db_session, tenant, monkeypatch):
    monkeypatch.setattr(settings, "HYBRID_ENABLED", False)
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    coll = _coll(db_session, tenant)
    emb, vs, cache = FakeEmbedder(), InMemoryVectorStore(), _cache()
    doc, _, _ = ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "Doc", "vacation policy", emb, vs, cache=cache)

    kw = dict(db=db_session, tenant_id=tenant.id, collection_id=coll.id,
              query="vacation", top_k=3, embedder=emb, vector_store=vs, cache=cache)
    retrieval.search_chunks(**kw)
    key = cache.retrieval_key(tenant.id, coll.id, "vacation", 3, "h0r0")
    assert cache.get_json(key) is not None

    ingestion.delete_document(db_session, tenant.id, doc.id, vs, cache=cache)  # CACHE-04
    assert cache.get_json(key) is None
