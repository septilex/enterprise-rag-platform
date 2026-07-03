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
