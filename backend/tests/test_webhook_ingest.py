"""ING-03: webhook-triggered ingestion reflected immediately."""


def test_webhook_ingest_reflected_without_scheduled_run(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "wh"}).json()["id"]

    r = api_client.post("/ingest/webhook", json={
        "tenant_id": tid, "collection_id": cid,
        "source_type": "text_batch",
        "config": {"documents": [
            {"title": "A", "content": "vacation policy grants twenty days", "source_uri": "s://a"},
        ]},
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"source_type": "text_batch", "seen": 1, "ingested": 1, "reused": 0}

    # Immediately searchable (event-driven, no polling wait).
    s = api_client.post("/search", json={
        "tenant_id": tid, "collection_id": cid, "query": "vacation policy", "top_k": 5})
    assert s.json()["total"] > 0


def test_webhook_unknown_connector_400(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "wh2"}).json()["id"]
    r = api_client.post("/ingest/webhook", json={
        "tenant_id": tid, "collection_id": cid, "source_type": "nope", "config": {}})
    assert r.status_code == 400
