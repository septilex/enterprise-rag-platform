"""MON-02 Prometheus metrics + MON-04 query logging."""

import json
import logging

CONTENT = ("Vacation policy grants twenty paid days per year. " * 6)


def test_metrics_endpoint_exposes_prometheus(api_client, tenant):
    # generate some traffic
    api_client.post("/collections", json={"tenant_id": str(tenant.id), "name": "m"})
    r = api_client.get("/metrics")
    assert r.status_code == 200
    assert "rag_requests_total" in r.text
    assert "rag_request_latency_seconds" in r.text


def test_chat_emits_structured_query_log(api_client, tenant, caplog):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "ql"}).json()["id"]
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "P", "content": CONTENT})

    with caplog.at_level(logging.INFO, logger="rag.query"):
        api_client.post("/chat", json={
            "tenant_id": tid, "collection_id": cid, "query": "vacation days"})

    records = [json.loads(r.message) for r in caplog.records
               if r.name == "rag.query"]
    assert records, "no rag.query log emitted"
    rec = records[-1]
    assert rec["event"] == "rag_query"
    assert rec["query"] == "vacation days"
    assert "retrieved_chunk_ids" in rec and "latency_ms" in rec
    assert rec["tenant_id"] == tid
