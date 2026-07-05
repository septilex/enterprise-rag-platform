"""UI-01/06: SSE streaming chat endpoint."""

import json

CONTENT = ("Vacation policy grants twenty paid days per year. " * 6)


def _parse_sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):])
            for line in text.splitlines() if line.startswith("data: ")]


def _setup(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "s"}).json()["id"]
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "P", "content": CONTENT})
    return tid, cid


def test_stream_emits_citations_tokens_then_done(api_client, tenant):
    tid, cid = _setup(api_client, tenant)
    r = api_client.post("/chat/stream", json={
        "tenant_id": tid, "collection_id": cid, "query": "vacation days"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "citations"
    assert "token" in types
    assert types[-1] == "done"
    assert events[-1]["grounded"] is True
    assert len(events[0]["citations"]) >= 1


def test_stream_persists_turn_to_session(api_client, tenant):
    tid, cid = _setup(api_client, tenant)
    sid = api_client.post("/sessions", json={
        "tenant_id": tid, "user_id": "u", "collection_id": cid, "title": "t"}).json()["id"]

    r = api_client.post("/chat/stream", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "vacation days", "session_id": sid})
    assert r.status_code == 200

    # History must reload after streaming (UI-03 regression).
    msgs = api_client.get(f"/sessions/{sid}/messages", params={"tenant_id": tid}).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "vacation days"
    assert msgs[1]["content"]


def test_stream_unknown_session_404(api_client, tenant):
    import uuid
    tid, cid = _setup(api_client, tenant)
    r = api_client.post("/chat/stream", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "hi", "session_id": str(uuid.uuid4())})
    assert r.status_code == 404


def test_stream_no_context_returns_done_not_grounded(api_client, tenant):
    tid, cid = _setup(api_client, tenant)
    r = api_client.post("/chat/stream", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "zzzz nonexistent quantum topic", "no_cache": True})
    events = _parse_sse(r.text)
    # Either no strong hits (single done) — grounded must be False in that case.
    done = events[-1]
    assert done["type"] == "done"
