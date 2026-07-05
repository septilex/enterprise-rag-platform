"""Item 6: cloud-connector retry / rate-limit edge-case hardening."""

import pytest

from app.services import connectors as C


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class _HTTPErr(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"http {status}")
        self.response = _Resp(status, headers)


def test_retry_succeeds_after_transient_429(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _HTTPErr(429, {"Retry-After": "0"})
        return "ok"

    assert C._with_retry(flaky, what="test") == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_and_raises(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda *_: None)

    def always_503():
        raise _HTTPErr(503)

    with pytest.raises(_HTTPErr):
        C._with_retry(always_503, attempts=3, what="test")


def test_non_retryable_propagates_immediately():
    calls = {"n": 0}

    def not_found():
        calls["n"] += 1
        raise _HTTPErr(404)

    with pytest.raises(_HTTPErr):
        C._with_retry(not_found, what="test")
    assert calls["n"] == 1  # no retries on 404


def test_throttle_message_is_retryable(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def throttled():
        calls["n"] += 1
        if calls["n"] < 2:
            raise Exception("Rate exceeded: SlowDown")
        return "ok"

    assert C._with_retry(throttled, what="test") == "ok"
