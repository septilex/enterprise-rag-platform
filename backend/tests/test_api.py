"""End-to-end HTTP contract test through the FastAPI app (fakes + real DB).

Guards the API surface — including FastAPI's lazy router include — which was
previously untested.
"""

CONTENT = (
    "The vacation policy grants twenty days of paid leave each year. "
    "Sick leave is ten days annually and requires manager approval. "
) * 5


def test_full_ingest_search_chat_delete_flow(api_client, tenant):
    tid = str(tenant.id)

    # create collection
    r = api_client.post("/collections", json={"tenant_id": tid, "name": "handbook"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    # ingest text
    r = api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid,
        "title": "Policy", "content": CONTENT})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["chunks_created"] > 0 and body["reused"] is False
    doc_id = body["document_id"]

    # re-ingest identical -> idempotent (ING-04)
    r = api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid,
        "title": "Policy", "content": CONTENT})
    assert r.json()["reused"] is True

    # search returns hits
    r = api_client.post("/search", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "vacation policy", "top_k": 5})
    assert r.status_code == 200
    assert r.json()["total"] > 0

    # grounded chat with citations (RET-07)
    r = api_client.post("/chat", json={
        "tenant_id": tid, "collection_id": cid, "query": "how many vacation days"})
    assert r.status_code == 200
    chat = r.json()
    assert chat["grounded"] is True
    assert len(chat["citations"]) >= 1

    # delete propagation (ING-08)
    r = api_client.delete(f"/documents/{doc_id}", params={"tenant_id": tid})
    assert r.status_code == 200 and r.json()["deleted"] is True

    # search now empty
    r = api_client.post("/search", json={
        "tenant_id": tid, "collection_id": cid,
        "query": "vacation policy", "top_k": 5})
    assert r.json()["total"] == 0


def test_delete_unknown_document_returns_404(api_client, tenant):
    import uuid
    r = api_client.delete(f"/documents/{uuid.uuid4()}", params={"tenant_id": str(tenant.id)})
    assert r.status_code == 404
