"""CACHE-06: cache hit/miss + savings exposed as metrics."""

import fakeredis

from app.core.config import settings
from app.db.models import Collection
from app.observability import CACHE_HITS, CACHE_MISSES
from app.services import ingestion, retrieval
from app.services.cache import RedisCache
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="cm-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_retrieval_cache_hit_and_miss_counted(db_session, tenant, monkeypatch):
    monkeypatch.setattr(settings, "HYBRID_ENABLED", False)
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    cache = RedisCache(fakeredis.FakeStrictRedis(decode_responses=True))
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P", "vacation policy days", emb, vs)

    miss_before = CACHE_MISSES.labels("retrieval")._value.get()
    hit_before = CACHE_HITS.labels("retrieval")._value.get()

    kw = dict(db=db_session, tenant_id=tenant.id, collection_id=coll.id,
              query="vacation", top_k=3, embedder=emb, vector_store=vs, cache=cache)
    retrieval.search_chunks(**kw)   # miss -> populates
    retrieval.search_chunks(**kw)   # hit

    assert CACHE_MISSES.labels("retrieval")._value.get() == miss_before + 1
    assert CACHE_HITS.labels("retrieval")._value.get() == hit_before + 1


def test_metrics_endpoint_exposes_cache_series(api_client, tenant):
    body = api_client.get("/metrics").text
    assert "rag_cache_hits_total" in body
    assert "rag_cache_misses_total" in body
    assert "rag_cache_upstream_calls_saved_total" in body
