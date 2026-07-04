"""SEC-05: administrative actions produce audit log entries."""

import json
import logging


def _records(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == "rag.audit"]


def test_collection_and_delete_are_audited(api_client, tenant, caplog):
    tid = str(tenant.id)
    with caplog.at_level(logging.INFO, logger="rag.audit"):
        cid = api_client.post("/collections", json={"tenant_id": tid, "name": "aud"}).json()["id"]
        doc = api_client.post("/documents/text", json={
            "tenant_id": tid, "collection_id": cid, "title": "P",
            "content": "vacation policy twenty days"}).json()["document_id"]
        api_client.delete(f"/documents/{doc}", params={"tenant_id": tid})

    actions = {r["action"] for r in _records(caplog)}
    assert "collection.create" in actions
    assert "document.delete" in actions
    for rec in _records(caplog):
        assert rec["event"] == "audit" and "actor" in rec and "target" in rec
