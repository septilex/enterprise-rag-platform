"""RET-06 / RET-07: multi-chunk context assembly, dedup, token budget, citations."""

import uuid

import app.services.generation as gen
from app.core.config import settings
from tests.fakes import FakeEmbedder, FakeLLM, InMemoryVectorStore


def _hit(content: str, score: float = 0.9) -> dict:
    return {
        "chunk_id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "chunk_index": 0,
        "content": content,
        "score": score,
        "doc_metadata": {},
    }


def _run(monkeypatch, hits):
    monkeypatch.setattr(gen, "search_chunks", lambda **kw: hits)
    return gen.generate_answer(
        db=None,
        query="q",
        tenant_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        embedder=FakeEmbedder(),
        vector_store=InMemoryVectorStore(),
        llm=FakeLLM(),
    )


def test_multiple_distinct_chunks_produce_multiple_citations(monkeypatch):
    hits = [_hit("Vacation policy is 20 days."), _hit("Sick leave is 10 days.")]
    grounded, answer, citations = _run(monkeypatch, hits)
    assert grounded is True
    assert len(citations) == 2
    assert citations[0]["index"] == 1 and citations[1]["index"] == 2
    assert len({c["chunk_id"] for c in citations}) == 2  # distinct chunks


def test_near_duplicate_chunks_are_collapsed(monkeypatch):
    hits = [_hit("The vacation policy grants 20 days off per year."),
            _hit("The vacation policy grants 20 days off per year."),  # dup
            _hit("Sick leave is 10 days.")]
    _, _, citations = _run(monkeypatch, hits)
    assert len(citations) == 2  # duplicate removed


def test_chunk_cap_is_respected(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_MAX_CONTEXT_CHUNKS", 3)
    hits = [_hit(f"Distinct fact number {i} about topic {i}.") for i in range(10)]
    _, _, citations = _run(monkeypatch, hits)
    assert len(citations) == 3


def test_token_budget_caps_context(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_MAX_CONTEXT_CHUNKS", 50)
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_BUDGET", 20)
    # Distinct (non-duplicate) ~7-token chunks; only ~2 fit in a 20-token budget.
    subjects = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
                "golf", "hotel", "india", "juliet"]
    hits = [_hit(f"{s} reports quarterly revenue figure number {i}")
            for i, s in enumerate(subjects)]
    _, _, citations = _run(monkeypatch, hits)
    assert 0 < len(citations) < 10  # budget bit before all 10 fit


def test_top_chunk_always_survives_even_if_over_budget(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_BUDGET", 5)
    hits = [_hit("word " * 200)]  # single hit far larger than the budget
    grounded, _, citations = _run(monkeypatch, hits)
    assert grounded is True  # not a false no-answer
    assert len(citations) == 1


def test_low_confidence_returns_no_answer(monkeypatch):
    hits = [_hit("irrelevant", score=0.01)]  # below MIN_RETRIEVAL_SCORE
    grounded, answer, citations = _run(monkeypatch, hits)
    assert grounded is False
    assert citations == []
    assert answer == settings.NO_ANSWER_MESSAGE


def test_no_hits_returns_no_answer(monkeypatch):
    grounded, answer, citations = _run(monkeypatch, [])
    assert grounded is False
    assert citations == []
