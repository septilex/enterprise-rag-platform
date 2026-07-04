"""SEC-02: RBAC — a key scoped to one collection cannot reach another."""

import json
import uuid

from app.core.config import settings
from app.db.models import Collection


def _make_collections(api_client, tenant):
    tid = str(tenant.id)
    a = api_client.post("/collections", json={"tenant_id": tid, "name": "A"}).json()["id"]
    b = api_client.post("/collections", json={"tenant_id": tid, "name": "B"}).json()["id"]
    return tid, a, b


def test_principal_scoped_to_collection_a_denied_on_b(api_client, tenant, monkeypatch):
    tid, a, b = _make_collections(api_client, tenant)

    principals = {
        "key-a": {"tenant_id": tid, "collections": [a]},
    }
    monkeypatch.setattr(settings, "PRINCIPALS_JSON", json.dumps(principals))
    headers = {"X-API-Key": "key-a"}

    # Allowed on collection A
    ra = api_client.post("/search", headers=headers, json={
        "tenant_id": tid, "collection_id": a, "query": "x"})
    assert ra.status_code == 200

    # Denied on collection B (SEC-02 acceptance)
    rb = api_client.post("/search", headers=headers, json={
        "tenant_id": tid, "collection_id": b, "query": "x"})
    assert rb.status_code == 403


def test_principal_denied_on_other_tenant(api_client, tenant, monkeypatch):
    tid, a, _ = _make_collections(api_client, tenant)
    principals = {"key-a": {"tenant_id": tid, "collections": "*"}}
    monkeypatch.setattr(settings, "PRINCIPALS_JSON", json.dumps(principals))

    other_tenant = str(uuid.uuid4())
    r = api_client.post("/search", headers={"X-API-Key": "key-a"}, json={
        "tenant_id": other_tenant, "collection_id": a, "query": "x"})
    assert r.status_code == 403


def test_unknown_key_rejected_401(api_client, tenant, monkeypatch):
    tid, a, _ = _make_collections(api_client, tenant)
    monkeypatch.setattr(settings, "PRINCIPALS_JSON",
                        json.dumps({"key-a": {"tenant_id": tid, "collections": "*"}}))
    r = api_client.post("/search", headers={"X-API-Key": "bad"}, json={
        "tenant_id": tid, "collection_id": a, "query": "x"})
    assert r.status_code == 401


def test_non_admin_cannot_create_tenant(api_client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "PRINCIPALS_JSON",
                        json.dumps({"key-a": {"tenant_id": str(tenant.id), "collections": "*"}}))
    r = api_client.post("/tenants", headers={"X-API-Key": "key-a"}, json={"name": "new"})
    assert r.status_code == 403
