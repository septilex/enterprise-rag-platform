"""ING-10: background re-embed migration; old index serves until cutover."""

from app.db.models import Chunk, Collection
from app.services import ingestion, retrieval
from app.services.reindex import reindex_collection
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="reindex-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_reindex_into_new_store_leaves_old_serving(db_session, tenant):
    coll = _coll(db_session, tenant)
    old_emb, old_store = FakeEmbedder(dim=64), InMemoryVectorStore()
    ingestion.ingest_text_document(
        db_session, tenant.id, coll.id, "P", "vacation policy twenty days", old_emb, old_store)
    n_chunks = db_session.query(Chunk).filter_by(collection_id=coll.id).count()

    # New embedding model (different dim) -> re-embed into a fresh target index.
    new_emb, new_store = FakeEmbedder(dim=128), InMemoryVectorStore()
    migrated = reindex_collection(db_session, tenant.id, coll.id, new_emb, new_store)

    assert migrated == n_chunks
    assert len(new_store.points) == n_chunks
    # Old index is untouched and still serves queries during the migration.
    old_hits = retrieval.search_chunks(
        db=db_session, tenant_id=tenant.id, collection_id=coll.id,
        query="vacation", top_k=5, embedder=old_emb, vector_store=old_store)
    assert old_hits

    # After cutover, the new index answers queries too.
    new_hits = retrieval.search_chunks(
        db=db_session, tenant_id=tenant.id, collection_id=coll.id,
        query="vacation", top_k=5, embedder=new_emb, vector_store=new_store)
    assert new_hits
