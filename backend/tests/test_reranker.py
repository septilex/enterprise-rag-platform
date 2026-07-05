"""RET-03 heuristic reranker + modular strategy selection (no torch)."""

from app.core.config import settings
from app.services.reranker import HeuristicReranker, get_reranker


def _cand(chunk_id, content, score):
    return {"chunk_id": chunk_id, "content": content, "score": score}


def test_heuristic_reranker_promotes_term_match():
    rr = HeuristicReranker()
    cands = [
        _cand("a", "completely unrelated text about weather", 0.9),
        _cand("b", "the vacation policy grants twenty vacation days", 0.4),
    ]
    ranked = rr.rerank("vacation days policy", cands, top_k=2)
    assert ranked[0]["chunk_id"] == "b"  # lexical match beats higher dense score


def test_get_reranker_strategy_selection(monkeypatch):
    monkeypatch.setattr(settings, "RERANK_STRATEGY", "none")
    assert get_reranker() is None
    monkeypatch.setattr(settings, "RERANK_STRATEGY", "heuristic")
    assert isinstance(get_reranker(), HeuristicReranker)
    assert get_reranker("none") is None


def test_reranker_empty_candidates():
    assert HeuristicReranker().rerank("q", [], 5) == []
