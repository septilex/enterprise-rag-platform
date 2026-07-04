"""In-memory fakes implementing the production interfaces.

These let us exercise ingestion / retrieval / generation / caching end-to-end
without OpenAI, Qdrant, or a live Redis — every slice stays verifiable in CI.
"""

from __future__ import annotations

import math
import re
import uuid

from app.services.embedder import Embedder
from app.services.llm import LLMClient
from app.services.vector_store import VectorStore

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class FakeEmbedder(Embedder):
    """Deterministic bag-of-words embedder into a fixed-dim space.

    Same text -> same vector; lexically similar texts -> nearby vectors,
    which is enough to make cosine ranking meaningful in tests.
    """

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.calls: list[list[str]] = []

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _tokens(text):
            v[hash(tok) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]


class InMemoryVectorStore(VectorStore):
    """Cosine-similarity vector store with exact-match payload filtering."""

    def __init__(self):
        self.points: dict[str, dict] = {}  # id -> {vector, payload}
        self.ensured = False

    def ensure_collection(self) -> None:
        self.ensured = True

    def upsert(self, ids, vectors, payloads) -> None:
        for pid, vec, payload in zip(ids, vectors, payloads):
            self.points[str(pid)] = {"vector": list(vec), "payload": dict(payload)}

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    def search(self, vector, filters, top_k: int = 5) -> list[dict]:
        hits = []
        for pid, rec in self.points.items():
            payload = rec["payload"]
            if any(payload.get(k) != v for k, v in filters.items()):
                continue
            hits.append(
                {
                    "id": pid,
                    "score": self._cosine(vector, rec["vector"]),
                    "payload": payload,
                }
            )
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:top_k]

    def delete(self, ids) -> None:  # supports ING-08 tests
        for pid in ids:
            self.points.pop(str(pid), None)


class FakeLLM(LLMClient):
    """Returns a canned grounded answer that echoes the first citation marker."""

    def __init__(self, answer: str = "Grounded answer [1]."):
        self.answer = answer
        self.calls: list[dict] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        return self.answer

    def stream(self, system: str, user: str):
        self.calls.append({"system": system, "user": user})
        for token in self.answer.split(" "):
            yield token + " "
