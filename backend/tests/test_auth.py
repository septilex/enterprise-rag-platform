"""SEC-01: API-key auth on all endpoints (401 when unauthenticated)."""

from app.core.config import settings


def test_no_keys_configured_allows_access(api_client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "")
    r = api_client.post("/collections", json={"tenant_id": str(tenant.id), "name": "c1"})
    assert r.status_code == 201


def test_missing_key_rejected_when_configured(api_client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "secret-key-1,secret-key-2")
    r = api_client.post("/collections", json={"tenant_id": str(tenant.id), "name": "c2"})
    assert r.status_code == 401


def test_wrong_key_rejected(api_client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "secret-key-1")
    r = api_client.post(
        "/collections",
        json={"tenant_id": str(tenant.id), "name": "c3"},
        headers={"X-API-Key": "nope"},
    )
    assert r.status_code == 401


def test_valid_key_accepted(api_client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "secret-key-1")
    r = api_client.post(
        "/collections",
        json={"tenant_id": str(tenant.id), "name": "c4"},
        headers={"X-API-Key": "secret-key-1"},
    )
    assert r.status_code == 201


def test_health_stays_public(api_client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "secret-key-1")
    assert api_client.get("/health").status_code == 200
