"""UI-03: multi-session chat history persisted across reloads."""

CONTENT = ("Vacation policy grants twenty paid days per year. " * 6)


def _setup(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "sx"}).json()["id"]
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "P", "content": CONTENT})
    return tid, cid


def test_session_persists_turns_and_history(api_client, tenant):
    tid, cid = _setup(api_client, tenant)

    sid = api_client.post("/sessions", json={
        "tenant_id": tid, "user_id": "alice", "collection_id": cid,
        "title": "Leave questions"}).json()["id"]

    api_client.post("/chat", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "vacation days", "session_id": sid})

    # History restored on reload.
    msgs = api_client.get(f"/sessions/{sid}/messages", params={"tenant_id": tid}).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "vacation days"
    assert len(msgs[1]["citations"]) >= 1


def test_sessions_listed_per_user(api_client, tenant):
    tid, cid = _setup(api_client, tenant)
    api_client.post("/sessions", json={"tenant_id": tid, "user_id": "bob", "title": "A"})
    api_client.post("/sessions", json={"tenant_id": tid, "user_id": "bob", "title": "B"})
    api_client.post("/sessions", json={"tenant_id": tid, "user_id": "carol", "title": "C"})

    bob = api_client.get("/sessions", params={"tenant_id": tid, "user_id": "bob"}).json()
    assert len(bob) == 2
    assert {s["title"] for s in bob} == {"A", "B"}


def test_chat_with_unknown_session_404(api_client, tenant):
    import uuid
    tid, cid = _setup(api_client, tenant)
    r = api_client.post("/chat", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "vacation days", "session_id": str(uuid.uuid4())})
    assert r.status_code == 404
