"""ING-05: configurable per-collection chunking."""

import pytest

from app.db.models import Collection
from app.services import ingestion
from app.services.chunking import chunk_document
from tests.fakes import FakeEmbedder, InMemoryVectorStore

MARKDOWN = """# Title

First paragraph about vacation policy and how it accrues over time.

# Section Two

Second paragraph about sick leave and manager approval requirements.
"""


def test_fixed_strategy_respects_config_size():
    text = "abcdefghij" * 20  # 200 chars
    pieces = chunk_document(text, "fixed", {"chunk_size": 50, "overlap": 0})
    assert all(len(p) <= 50 for p in pieces)
    assert len(pieces) == 4


def test_structure_strategy_splits_on_blocks():
    pieces = chunk_document(MARKDOWN, "structure", {"max_chars": 120})
    # Each heading+paragraph forms its own block; boundaries differ from fixed.
    assert len(pieces) >= 2
    assert any(p.startswith("# Title") for p in pieces)


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        chunk_document("hello", "does-not-exist")


def test_two_collections_produce_different_boundaries(db_session, tenant):
    """ING-05 acceptance: different chunking configs -> different boundaries."""
    emb, vs = FakeEmbedder(), InMemoryVectorStore()

    fixed = Collection(tenant_id=tenant.id, name="fixed-coll",
                       chunking_strategy="fixed",
                       chunking_config={"chunk_size": 60, "overlap": 0})
    structure = Collection(tenant_id=tenant.id, name="structure-coll",
                           chunking_strategy="structure",
                           chunking_config={"max_chars": 200})
    db_session.add_all([fixed, structure])
    db_session.commit()

    _, n_fixed, _ = ingestion.ingest_text_document(
        db_session, tenant.id, fixed.id, "Doc", MARKDOWN, emb, vs)
    _, n_struct, _ = ingestion.ingest_text_document(
        db_session, tenant.id, structure.id, "Doc", MARKDOWN, emb, vs)

    assert n_fixed != n_struct  # boundaries genuinely differ
