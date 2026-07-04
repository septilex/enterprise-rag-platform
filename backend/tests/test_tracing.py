"""MON-01: a single chat query emits correlated spans across stages."""

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.tracing import setup_tracing

CONTENT = ("Vacation policy grants twenty paid days per year. " * 6)


@pytest.fixture
def spans():
    provider = setup_tracing()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


def test_chat_query_spans_cover_all_stages(api_client, tenant, spans):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "tr"}).json()["id"]
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "P", "content": CONTENT})
    spans.clear()  # ignore ingest spans; focus on the query trace

    r = api_client.post("/chat", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "vacation days", "no_cache": True})
    assert r.status_code == 200

    names = {s.name for s in spans.get_finished_spans()}
    assert "chat.request" in names
    assert "retrieval.dense" in names
    assert "generation.llm" in names

    # All query spans share ONE trace id (end-to-end correlation).
    trace_ids = {s.context.trace_id for s in spans.get_finished_spans()}
    assert len(trace_ids) == 1


def test_ingestion_emits_embed_span(api_client, tenant, spans):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "tr2"}).json()["id"]
    spans.clear()
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "P", "content": CONTENT})
    names = {s.name for s in spans.get_finished_spans()}
    assert "ingestion.embed" in names
