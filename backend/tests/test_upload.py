"""UI-07: file upload as retrieval context, scoped to tenant/collection."""


def _coll(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "up"}).json()["id"]
    return tid, cid


def test_upload_text_file_is_ingested_and_searchable(api_client, tenant):
    tid, cid = _coll(api_client, tenant)
    content = b"Vacation policy grants twenty paid days per year for all staff. " * 4

    r = api_client.post(
        "/documents/upload",
        data={"tenant_id": tid, "collection_id": cid},
        files={"file": ("policy.txt", content, "text/plain")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "embedded" and body["chunks_created"] > 0

    s = api_client.post("/search", json={
        "tenant_id": tid, "collection_id": cid, "query": "vacation policy", "top_k": 5})
    assert s.json()["total"] > 0


def test_upload_tags_session_metadata(api_client, tenant):
    import uuid
    tid, cid = _coll(api_client, tenant)
    sid = str(uuid.uuid4())
    r = api_client.post(
        "/documents/upload",
        data={"tenant_id": tid, "collection_id": cid, "session_id": sid},
        files={"file": ("notes.md", b"grounded fact about alpha topic here", "text/markdown")},
    )
    assert r.status_code == 201
    # metadata filter by session returns the uploaded doc's chunks
    s = api_client.post("/search", json={
        "tenant_id": tid, "collection_id": cid, "query": "alpha topic",
        "top_k": 5, "metadata_filter": {"session_id": sid}})
    assert s.json()["total"] > 0


def test_upload_binary_file_is_quarantined(api_client, tenant):
    tid, cid = _coll(api_client, tenant)
    r = api_client.post(
        "/documents/upload",
        data={"tenant_id": tid, "collection_id": cid},
        files={"file": ("bad.bin", bytes(range(8)) * 4, "application/octet-stream")},
    )
    assert r.status_code == 201
    assert r.json()["status"] == "quarantined"
