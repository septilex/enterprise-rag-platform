"""ING-01: pluggable connector framework."""

import os

from app.db.models import Chunk, Collection
from app.services import connectors
from app.services.connectors import (
    Connector,
    SourceDocument,
    available_connectors,
    get_connector,
    register_connector,
    run_connector,
)
from tests.fakes import FakeEmbedder, InMemoryVectorStore


def _coll(db, tenant):
    c = Collection(tenant_id=tenant.id, name="conn-coll")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_registry_lists_builtin_connectors():
    avail = available_connectors()
    assert "text_batch" in avail and "filesystem" in avail


def test_custom_connector_registers_without_core_changes():
    @register_connector
    class DummyConnector(Connector):
        source_type = "dummy_test_src"

        def fetch(self):
            yield SourceDocument("t", "hello world", "dummy://1")

    assert "dummy_test_src" in available_connectors()
    conn = get_connector("dummy_test_src")
    assert next(conn.fetch()).content == "hello world"


def test_run_connector_ingests_and_is_idempotent(db_session, tenant):
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()
    docs = [
        {"title": "A", "content": "vacation policy twenty days", "source_uri": "s://a"},
        {"title": "B", "content": "sick leave ten days", "source_uri": "s://b"},
    ]

    first = run_connector(db_session, get_connector("text_batch", documents=docs),
                          tenant.id, coll.id, emb, vs)
    assert first == {"source_type": "text_batch", "seen": 2, "ingested": 2, "reused": 0}
    chunk_count = db_session.query(Chunk).filter_by(collection_id=coll.id).count()

    second = run_connector(db_session, get_connector("text_batch", documents=docs),
                           tenant.id, coll.id, emb, vs)
    assert second["reused"] == 2 and second["ingested"] == 0  # ING-04 idempotent
    assert db_session.query(Chunk).filter_by(collection_id=coll.id).count() == chunk_count


def test_filesystem_connector_reads_files(tmp_path, db_session, tenant):
    (tmp_path / "doc1.md").write_text("# Title\nvacation policy content", encoding="utf-8")
    (tmp_path / "skip.bin").write_text("ignored", encoding="utf-8")
    coll = _coll(db_session, tenant)
    emb, vs = FakeEmbedder(), InMemoryVectorStore()

    summary = run_connector(db_session, get_connector("filesystem", root=str(tmp_path)),
                            tenant.id, coll.id, emb, vs)
    assert summary["seen"] == 1 and summary["ingested"] == 1  # only the .md
