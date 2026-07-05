"""Production hardening: OIDC SSO flow, scheduler, JSON logs, connector registry."""

import logging
import time

import jwt
import pytest

from app.core.config import settings
from app.db.models import Collection, Source
from app.services import connectors, ingestion_runs, oidc


# ---------- OIDC / SSO (SEC-01) ----------

@pytest.fixture
def oidc_dev(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "oidc")
    monkeypatch.setattr(settings, "OIDC_DEV_SECRET", "test-secret")
    monkeypatch.setattr(settings, "OIDC_JWKS_URL", "")
    monkeypatch.setattr(settings, "OIDC_AUDIENCE", "")
    oidc.reset_verifier()
    yield
    oidc.reset_verifier()


def _bearer(email, secret="test-secret"):
    token = jwt.encode({"email": email, "name": email, "exp": int(time.time()) + 300},
                       secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_oidc_missing_token_rejected(api_client, oidc_dev):
    assert api_client.get("/me").status_code == 401


def test_oidc_invalid_signature_rejected(api_client, oidc_dev):
    r = api_client.get("/me", headers=_bearer("x@corp.test", secret="wrong-secret"))
    assert r.status_code == 401


def test_oidc_valid_token_provisions_user(api_client, oidc_dev, db_session):
    r = api_client.get("/me", headers=_bearer("sso@corp.test"))
    assert r.status_code == 200
    assert r.json()["email"] == "sso@corp.test"
    # auto-provisioned user with no memberships yet
    assert r.json()["tenants"] == []


def test_oidc_dev_verifier_roundtrip():
    v = oidc.DevJWTVerifier("s")
    tok = jwt.encode({"email": "a@b.c"}, "s", algorithm="HS256")
    assert v.verify(tok)["email"] == "a@b.c"
    with pytest.raises(oidc.TokenError):
        v.verify("not-a-token")


# ---------- Scheduler (ING-03) ----------

def _sched_source(db, tenant, interval, last_success=None):
    coll = Collection(tenant_id=tenant.id, name=f"sc-{time.time_ns()}")
    db.add(coll); db.commit(); db.refresh(coll)
    src = ingestion_runs.get_or_create_source(
        db, tenant.id, coll.id, "s3_mock", "Sched",
        config={"connector_type": "s3_mock", "objects": {"a.txt": "content"},
                "schedule_seconds": interval})
    if last_success is not None:
        src.last_success_at = last_success
        db.commit()
    return src


def test_due_sources_respects_interval(db_session, tenant):
    from datetime import datetime, timedelta, timezone
    from app import scheduler

    # never run -> due
    s1 = _sched_source(db_session, tenant, interval=60)
    # recently succeeded -> not due
    s2 = _sched_source(db_session, tenant, interval=3600,
                       last_success=datetime.now(timezone.utc))
    # succeeded long ago -> due
    s3 = _sched_source(db_session, tenant, interval=60,
                       last_success=datetime.now(timezone.utc) - timedelta(hours=1))

    due_ids = {s.id for s in scheduler.due_sources(db_session)}
    assert s1.id in due_ids and s3.id in due_ids and s2.id not in due_ids


def test_scheduler_tick_is_idempotent(db_session, tenant):
    import fakeredis
    from app import scheduler
    from app.services.jobs import RedisJobQueue

    src = _sched_source(db_session, tenant, interval=60)
    q = RedisJobQueue(fakeredis.FakeStrictRedis(decode_responses=True))

    n1 = scheduler.tick(db_session, q)
    assert n1 == 1  # enqueued once, run now queued
    n2 = scheduler.tick(db_session, q)
    assert n2 == 0  # active run guard prevents a duplicate
    assert q.depth()["incremental"] == 1
    assert src  # referenced


# ---------- Structured JSON logging (MON-04) ----------

def test_json_log_format(monkeypatch, capsys):
    from app import observability
    monkeypatch.setattr(settings, "LOG_FORMAT", "json")
    observability.setup_logging()
    logging.getLogger("rag.test").info("hello world")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    import json
    rec = json.loads(line)
    assert rec["message"] == "hello world" and rec["level"] == "INFO"
    monkeypatch.setattr(settings, "LOG_FORMAT", "text")
    observability.setup_logging()  # restore


# ---------- Real connector adapters registered (ING-01) ----------

def test_real_connectors_registered():
    avail = connectors.available_connectors()
    assert "s3" in avail and "confluence" in avail and "s3_mock" in avail

    # constructing works; fetch fails cleanly without creds/deps (isolated adapter)
    c = connectors.get_connector("s3", bucket="b")
    assert c.source_type == "s3"
