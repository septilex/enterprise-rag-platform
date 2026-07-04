"""MON-08: per-tenant/collection cost attribution report."""

from app.db.models import Collection
from app.services import ingestion, generation
from app.services.usage import cost_report
from tests.fakes import FakeEmbedder, FakeLLM, InMemoryVectorStore


def _coll(db, tenant, name="cost-coll"):
    c = Collection(tenant_id=tenant.id, name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_usage_recorded_and_reported(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P", "vacation policy twenty days", emb, vs)
    generation.generate_answer(
        db=db_session, query="vacation days", tenant_id=tenant.id,
        collection_id=coll.id, embedder=emb, vector_store=vs,
        llm=FakeLLM(answer="Twenty [1]."))

    report = cost_report(db_session, tenant.id)
    kinds = {line["kind"] for line in report["lines"]}
    assert "embed_texts" in kinds and "llm_tokens" in kinds
    assert report["total_estimated_cost"] >= 0.0
    # every line is attributed to this tenant's collection
    assert all(line["collection_id"] == str(coll.id) for line in report["lines"])


def test_cost_endpoint(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "cc"}).json()["id"]
    api_client.post("/documents/text", json={
        "tenant_id": tid, "collection_id": cid, "title": "P",
        "content": "vacation policy twenty days"})
    r = api_client.get("/cost/report", params={"tenant_id": tid})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == tid
    assert any(line["kind"] == "embed_texts" for line in body["lines"])
