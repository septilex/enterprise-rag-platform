"""CACHE-02: semantic response cache returns cached answers for similar queries."""

import fakeredis

from app.core.config import settings
from app.db.models import Collection
from app.services import generation, ingestion
from app.services.cache import RedisCache
from tests.fakes import FakeEmbedder, FakeLLM, InMemoryVectorStore


def _cache():
    return RedisCache(fakeredis.FakeStrictRedis(decode_responses=True))


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="sem-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_paraphrase_hits_semantic_cache(db_session, tenant, monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_THRESHOLD", 0.6)
    coll = _coll(db_session, tenant)
    emb, vs, cache = FakeEmbedder(), InMemoryVectorStore(), _cache()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P",
        "Vacation policy grants twenty paid days per year for all staff.", emb, vs)

    llm = FakeLLM(answer="You get twenty vacation days [1].")
    kw = dict(db=db_session, tenant_id=tenant.id, collection_id=coll.id,
              embedder=emb, vector_store=vs, llm=llm, cache=cache)

    g1, a1, _ = generation.generate_answer(query="how many vacation days do staff get", **kw)
    assert g1 is True and len(llm.calls) == 1

    # A lexically similar paraphrase should hit the cache -> no new LLM call.
    g2, a2, _ = generation.generate_answer(query="how many vacation days staff get", **kw)
    assert a2 == a1
    assert len(llm.calls) == 1  # served from semantic cache (CACHE-02)


def test_no_cache_flag_bypasses_semantic_cache(db_session, tenant, monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_THRESHOLD", 0.6)
    coll = _coll(db_session, tenant)
    emb, vs, cache = FakeEmbedder(), InMemoryVectorStore(), _cache()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P", "Vacation policy grants twenty days.", emb, vs)

    llm = FakeLLM(answer="Twenty days [1].")
    kw = dict(db=db_session, tenant_id=tenant.id, collection_id=coll.id,
              embedder=emb, vector_store=vs, llm=llm, cache=cache)
    generation.generate_answer(query="vacation days", **kw)
    generation.generate_answer(query="vacation days", no_cache=True, **kw)  # CACHE-08
    assert len(llm.calls) == 2  # bypass forced a fresh generation
