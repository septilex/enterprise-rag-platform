"""Observability: Prometheus metrics (MON-02) and structured query logging (MON-04)."""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable

from app.core.config import settings

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Golden-signal metrics, labeled per route + method + status (MON-02).
REQUEST_LATENCY = Histogram(
    "rag_request_latency_seconds",
    "Request latency in seconds",
    labelnames=("method", "path", "status"),
)
REQUEST_COUNT = Counter(
    "rag_requests_total",
    "Total requests",
    labelnames=("method", "path", "status"),
)

# Cache effectiveness metrics (CACHE-06), labeled per layer.
CACHE_HITS = Counter("rag_cache_hits_total", "Cache hits", labelnames=("layer",))
CACHE_MISSES = Counter("rag_cache_misses_total", "Cache misses", labelnames=("layer",))
# Upstream calls avoided by the cache -> proxy for cost savings.
CACHE_CALLS_SAVED = Counter(
    "rag_cache_upstream_calls_saved_total",
    "Upstream (embedding/LLM/vector) calls avoided by caching",
    labelnames=("layer",),
)


# Drift monitoring (MON-05) + alerting signal (MON-06).
DRIFT_SCORE = Gauge("rag_query_drift_score", "Latest query-distribution drift score")
DRIFT_ALERTS = Counter("rag_query_drift_alerts_total", "Drift alerts raised")

# RAG-specific platform metrics (MON-02/03).
INGESTION_RUNS = Counter(
    "rag_ingestion_runs_total", "Ingestion runs by terminal status",
    labelnames=("status", "trigger"),
)
QUERIES = Counter(
    "rag_queries_total", "Chat/search queries per tenant", labelnames=("tenant", "kind"),
)
RETRIEVAL_LATENCY = Histogram(
    "rag_retrieval_latency_seconds", "Retrieval (embed+search+rerank) latency",
)


def record_ingestion_run(status: str, trigger: str) -> None:
    INGESTION_RUNS.labels(status, trigger).inc()


def record_query(tenant_id, kind: str) -> None:
    QUERIES.labels(str(tenant_id), kind).inc()


def record_cache(layer: str, hit: bool, saved: int = 0) -> None:
    if hit:
        CACHE_HITS.labels(layer).inc()
        if saved:
            CACHE_CALLS_SAVED.labels(layer).inc(saved)
    else:
        CACHE_MISSES.labels(layer).inc()

_query_logger = logging.getLogger("rag.query")
_audit_logger = logging.getLogger("rag.audit")


class _JsonLogFormatter(logging.Formatter):
    """Consistent structured JSON log lines for production log pipelines (MON-04)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = _request_id.get()
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging() -> None:
    """Configure root logging; JSON when settings.LOG_FORMAT == 'json'."""
    root = logging.getLogger()
    level = logging.INFO
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    if settings.LOG_FORMAT == "json":
        handler.setFormatter(_JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    root.addHandler(handler)

# PII patterns redacted from logged query text (MON-04).
_PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[CARD]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b(?:\+?\d[\d ().-]{7,}\d)\b"), "[PHONE]"),
]


def redact_pii(text: str) -> str:
    """Mask emails / card / SSN / phone numbers in free text (MON-04)."""
    for pattern, repl in _PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def audit_log(action: str, actor: str, target: dict) -> None:
    """Append an immutable audit record for an administrative action (SEC-05)."""
    _audit_logger.info(json.dumps({
        "event": "audit",
        "action": action,
        "actor": actor,
        "target": target,
    }))


import contextvars

# Correlation id propagated across a request: chat -> retrieval -> ingestion refs.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


def current_request_id() -> str:
    return _request_id.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assign/propagate an X-Request-ID for every request (MON-01 correlation)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = _request_id.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _request_id.reset(token)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record latency + count for every request (error rate derives from status)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            # Use the route template (not the raw path) to bound label cardinality.
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            elapsed = time.perf_counter() - start
            REQUEST_LATENCY.labels(request.method, path, status).observe(elapsed)
            REQUEST_COUNT.labels(request.method, path, status).inc()


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def log_query(
    *,
    tenant_id,
    collection_id,
    query: str,
    retrieved_chunk_ids: list,
    grounded: bool,
    answer: str,
    latency_ms: float,
) -> None:
    """Emit one structured JSON log line per answered query (MON-04).

    Captures input, retrieved context ids, output, and latency so a sampled
    query is fully reconstructable for audit/offline evaluation.
    """
    logged_query = redact_pii(query) if settings.LOG_PII_REDACTION else query
    record = {
        "event": "rag_query",
        "request_id": current_request_id() or str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "collection_id": str(collection_id),
        "query": logged_query,
        "retrieved_chunk_ids": [str(c) for c in retrieved_chunk_ids],
        "grounded": grounded,
        "answer_chars": len(answer),
        "latency_ms": round(latency_ms, 2),
    }
    _query_logger.info(json.dumps(record))
