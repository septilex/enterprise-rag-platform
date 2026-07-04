"""OpenTelemetry tracing across ingestion/retrieval/rerank/generation (MON-01).

A single query's trace links request -> generation -> retrieval (-> rerank)
spans under one trace id, giving end-to-end correlation. Export defaults to a
no-op provider; set OTEL_EXPORTER_OTLP_ENDPOINT to ship to Jaeger/Tempo.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from app.core.config import settings

_configured = False
_log = logging.getLogger("rag.tracing")


def _attach_exporters(provider: TracerProvider) -> None:
    """Wire span exporters based on config (MON-01).

    - OTEL_EXPORTER_OTLP_ENDPOINT set -> ship to an OTLP collector (Jaeger/Tempo).
    - OTEL_CONSOLE_EXPORT true -> also print spans (useful for local verification).
    Default (neither set) leaves a no-op provider.
    """
    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.strip()
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
            )
            _log.info("OTLP span exporter enabled -> %s", endpoint)
        except Exception as exc:  # pragma: no cover - env-dependent
            _log.warning("OTLP exporter unavailable: %s", exc)
    if settings.OTEL_CONSOLE_EXPORT:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))


def setup_tracing() -> TracerProvider:
    """Install a global TracerProvider once. Idempotent."""
    global _configured
    provider = trace.get_tracer_provider()
    if not _configured or not isinstance(provider, TracerProvider):
        provider = TracerProvider(
            resource=Resource.create({"service.name": "rag-platform"})
        )
        _attach_exporters(provider)
        trace.set_tracer_provider(provider)
        _configured = True
    return provider  # type: ignore[return-value]


def get_tracer():
    return trace.get_tracer("rag")


def span(name: str):
    """Context manager for a child span in the current trace."""
    return get_tracer().start_as_current_span(name)
