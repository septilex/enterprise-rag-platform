"""MON-07 / UI-05: feedback capture is persisted and listable."""

import uuid


def _coll(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "fb"}).json()["id"]
    return tid, cid


def test_thumbs_down_with_comment_persisted_and_listed(api_client, tenant):
    tid, cid = _coll(api_client, tenant)
    r = api_client.post("/feedback", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "vacation days?", "answer": "Twenty [1].",
        "rating": "down", "comment": "missed part-time staff",
        "chunk_ids": [str(uuid.uuid4())]})
    assert r.status_code == 201, r.text
    assert r.json()["rating"] == "down"

    lst = api_client.get("/feedback", params={"tenant_id": tid, "collection_id": cid})
    assert lst.status_code == 200
    rows = lst.json()
    assert len(rows) == 1
    assert rows[0]["comment"] == "missed part-time staff"


def test_invalid_rating_rejected(api_client, tenant):
    tid, cid = _coll(api_client, tenant)
    r = api_client.post("/feedback", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "q", "answer": "a", "rating": "meh"})
    assert r.status_code == 422
