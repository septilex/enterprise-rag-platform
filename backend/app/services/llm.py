"""Swappable LLM interface and OpenAI implementation."""

from abc import ABC, abstractmethod

from openai import OpenAI

from app.core.config import settings


class LLMClient(ABC):
    """Swappable LLM interface."""

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        ...


class OpenAILLM(LLMClient):
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_CHAT_MODEL

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        return resp.choices[0].message.content or ""
