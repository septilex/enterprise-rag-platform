"""QdrantVectorStore must split large upserts into bounded batches.

Regression: a 2,882-chunk document produced one multi-MB upsert request that
the client aborted/timed out, failing the whole ingestion run.
"""

import uuid
from unittest.mock import MagicMock

from app.services.vector_store import QdrantVectorStore


def _store_with_mock_client():
    store = QdrantVectorStore.__new__(QdrantVectorStore)  # skip real connect
    store.collection_name = "test"
    store._client = MagicMock()
    return store


def test_large_upsert_is_batched():
    store = _store_with_mock_client()
    n = QdrantVectorStore.UPSERT_BATCH * 2 + 10
    store.upsert(
        ids=[uuid.uuid4() for _ in range(n)],
        vectors=[[0.0]] * n,
        payloads=[{}] * n,
    )
    sizes = [len(c.kwargs["points"]) for c in store._client.upsert.call_args_list]
    assert sizes == [QdrantVectorStore.UPSERT_BATCH, QdrantVectorStore.UPSERT_BATCH, 10]


def test_small_upsert_single_call():
    store = _store_with_mock_client()
    store.upsert(ids=[uuid.uuid4()], vectors=[[0.0]], payloads=[{}])
    assert store._client.upsert.call_count == 1
