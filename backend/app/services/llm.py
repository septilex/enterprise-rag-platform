"""Swappable LLM interface and OpenAI implementation."""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from openai import OpenAI

from app.core.config import settings


class LLMClient(ABC):
    """Swappable LLM interface."""

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        ...

    @abstractmethod
    def stream(self, system: str, user: str) -> Iterator[str]:
        """Yield answer text incrementally (UI-01 token streaming)."""
        ...


class OpenAILLM(LLMClient):
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_CHAT_MODEL

    def _messages(self, system: str, user: str) -> list[dict]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, user),
            temperature=0,
        )
        return resp.choices[0].message.content or ""

    def stream(self, system: str, user: str) -> Iterator[str]:
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, user),
            temperature=0,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
