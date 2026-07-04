"""MON-04 PII redaction + MON-01 exporter wiring."""

import json
import logging

from app.core.config import settings
from app.observability import redact_pii


def test_redact_pii_masks_common_identifiers():
    out = redact_pii("mail me at jane.doe@acme.com or call 555-123-4567")
    assert "jane.doe@acme.com" not in out
    assert "[EMAIL]" in out and "[PHONE]" in out

    assert "[SSN]" in redact_pii("ssn 123-45-6789")
    assert "[CARD]" in redact_pii("card 4111 1111 1111 1111")


def test_query_log_is_redacted(api_client, tenant, caplog, monkeypatch):
    monkeypatch.setattr(settings, "LOG_PII_REDACTION", True)
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "pii"}).json()["id"]
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "P",
        "content": "vacation policy twenty days " * 6})

    with caplog.at_level(logging.INFO, logger="rag.query"):
        api_client.post("/chat", json={
            "tenant_id": tid, "collection_id": cid,
            "query": "email bob@example.com about vacation"})

    recs = [json.loads(r.message) for r in caplog.records if r.name == "rag.query"]
    assert recs
    assert "bob@example.com" not in recs[-1]["query"]
    assert "[EMAIL]" in recs[-1]["query"]


def test_console_exporter_wires_without_error(monkeypatch):
    # Reset the module-level guard so setup re-runs with console export on.
    import app.tracing as tracing
    monkeypatch.setattr(settings, "OTEL_CONSOLE_EXPORT", True)
    monkeypatch.setattr(tracing, "_configured", False)
    provider = tracing.setup_tracing()
    # At least one span processor is attached (no exception raised).
    assert provider is not None
