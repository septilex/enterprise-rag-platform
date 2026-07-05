"""MON-01/02: ingestion/query metrics + request correlation ID."""

from app.observability import INGESTION_RUNS, record_ingestion_run


def test_record_ingestion_run_increments():
    before = INGESTION_RUNS.labels("succeeded", "manual")._value.get()
    record_ingestion_run("succeeded", "manual")
    assert INGESTION_RUNS.labels("succeeded", "manual")._value.get() == before + 1


def test_metrics_endpoint_exposes_platform_series(api_client):
    body = api_client.get("/metrics").text
    assert "rag_ingestion_runs_total" in body
    assert "rag_queries_total" in body
    assert "rag_retrieval_latency_seconds" in body


def test_correlation_id_header_present(api_client):
    r = api_client.get("/health")
    assert r.headers.get("x-request-id")

    # supplied id is echoed back
    r2 = api_client.get("/health", headers={"X-Request-ID": "test-corr-123"})
    assert r2.headers.get("x-request-id") == "test-corr-123"


def test_query_counter_increments_on_chat(api_client, tenant):
    from app.observability import QUERIES
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "m"}).json()["id"]
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "P",
        "content": "vacation policy twenty days " * 6})
    before = QUERIES.labels(tid, "chat")._value.get()
    api_client.post("/chat", json={"tenant_id": tid, "collection_id": cid, "query": "vacation"})
    assert QUERIES.labels(tid, "chat")._value.get() == before + 1
