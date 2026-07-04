"""MON-05: query-distribution drift detection triggers an alert."""

from app.observability import DRIFT_ALERTS
from app.services import drift
from tests.fakes import FakeEmbedder


def test_no_drift_for_similar_distributions():
    emb = FakeEmbedder()
    ref = emb.embed(["vacation policy days", "vacation leave entitlement"])
    cur = emb.embed(["vacation days policy", "leave vacation entitlement"])
    drifted, score = drift.detect_drift(ref, cur, threshold=0.3)
    assert drifted is False


def test_shifted_distribution_flags_drift():
    emb = FakeEmbedder()
    ref = emb.embed(["vacation leave holiday paid time off"] * 3)
    cur = emb.embed(["kubernetes firmware calibration appliance XZ9000"] * 3)
    drifted, score = drift.detect_drift(ref, cur, threshold=0.3)
    assert drifted is True
    assert score > 0.3


def test_check_query_drift_raises_alert_metric():
    emb = FakeEmbedder()
    before = DRIFT_ALERTS._value.get()
    result = drift.check_query_drift(
        emb,
        reference_queries=["vacation leave holiday"],
        current_queries=["kubernetes firmware appliance"],
        threshold=0.2,
    )
    assert result["drifted"] is True
    assert DRIFT_ALERTS._value.get() == before + 1


def test_drift_endpoint(api_client, tenant):
    r = api_client.post("/monitoring/drift", json={
        "reference_queries": ["vacation leave holiday"],
        "current_queries": ["kubernetes firmware appliance"],
        "threshold": 0.2})
    assert r.status_code == 200
    assert r.json()["drifted"] is True
