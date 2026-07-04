"""INFRA-05: liveness + readiness probes."""


def test_health_liveness(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_readiness(api_client):
    # DB + Qdrant are up in the test environment -> ready.
    r = api_client.get("/ready")
    assert r.status_code == 200
    assert r.text == "ready"
