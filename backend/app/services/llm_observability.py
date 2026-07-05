"""Item 5 (MON-09): external LLM-observability adapter interface.

A thin, additive seam so prompt/response traces can be shipped to Langfuse /
Opik / Arize without touching generation logic. Ships a no-op default and a
webhook adapter; a vendor SDK adapter can be dropped in behind the same
interface. Selection is by config; disabled by default (no behavior change).
"""

from __future__ import annotations

import json
import logging
import urllib.request
import uuid
from abc import ABC, abstractmethod

from app.core.config import settings

log = logging.getLogger("rag.llmobs")


class LLMObservabilityAdapter(ABC):
    @abstractmethod
    def record_generation(
        self,
        *,
        request_id: str,
        tenant_id,
        collection_id,
        query: str,
        answer: str,
        grounded: bool,
        citations: list,
        latency_ms: float,
    ) -> None:
        ...


class NoopAdapter(LLMObservabilityAdapter):
    def record_generation(self, **_kwargs) -> None:  # default: do nothing
        return None


class WebhookAdapter(LLMObservabilityAdapter):
    """Ships a generation event as JSON to a collector endpoint (Langfuse/Opik/
    Arize ingestion webhooks, or an OTLP-logs sidecar). Best-effort; never raises."""

    def __init__(self, endpoint: str, api_key: str = "", timeout: float = 3.0):
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = timeout

    def record_generation(self, **event) -> None:
        try:
            payload = {"type": "rag.generation", "id": uuid.uuid4().hex, **{
                k: (str(v) if k in ("tenant_id", "collection_id") else v)
                for k, v in event.items()}}
            data = json.dumps(payload).encode()
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            req = urllib.request.Request(self._endpoint, data=data, headers=headers)
            urllib.request.urlopen(req, timeout=self._timeout)  # noqa: S310
        except Exception as exc:  # never break the request path
            log.warning("llm-observability export failed: %s", exc)


_adapter: LLMObservabilityAdapter | None = None


def get_adapter() -> LLMObservabilityAdapter:
    """Build the configured adapter once (no-op unless LLM_OBS_ENABLED)."""
    global _adapter
    if _adapter is not None:
        return _adapter
    if settings.LLM_OBS_ENABLED and settings.LLM_OBS_ENDPOINT:
        _adapter = WebhookAdapter(settings.LLM_OBS_ENDPOINT, settings.LLM_OBS_API_KEY)
        log.info("LLM observability export enabled -> %s", settings.LLM_OBS_ENDPOINT)
    else:
        _adapter = NoopAdapter()
    return _adapter


def reset_adapter() -> None:  # test helper
    global _adapter
    _adapter = None
