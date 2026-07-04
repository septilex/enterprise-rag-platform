"""Controlled re-embedding / re-index migration (ING-10).

Re-embeds all chunks of a collection into a *target* vector store (which may be
a fresh index for a new embedding model) while the existing index keeps serving
queries. The caller cuts over to the new store once the re-index completes, so
there is no service interruption.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import Chunk
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore
from app.tracing import span


def reindex_collection(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    embedder: Embedder,
    target_store: VectorStore,
    batch_size: int = 256,
) -> int:
    """Re-embed every chunk of a collection into ``target_store``.

    Returns the number of chunks re-embedded. Runs against a target index so
    the live index is untouched until cutover (ING-10).
    """
    target_store.ensure_collection()
    q = (
        db.query(Chunk)
        .filter(Chunk.tenant_id == tenant_id, Chunk.collection_id == collection_id)
        .order_by(Chunk.document_id, Chunk.chunk_index)
    )

    total = 0
    batch: list[Chunk] = []
    with span("reindex.collection"):
        for chunk in q.yield_per(batch_size):
            batch.append(chunk)
            if len(batch) >= batch_size:
                total += _flush(batch, embedder, target_store)
                batch = []
        if batch:
            total += _flush(batch, embedder, target_store)
    return total


def _flush(batch: list[Chunk], embedder: Embedder, target_store: VectorStore) -> int:
    vectors = embedder.embed([c.content for c in batch])
    payloads = [
        {
            "tenant_id": str(c.tenant_id),
            "collection_id": str(c.collection_id),
            "document_id": str(c.document_id),
            "chunk_index": c.chunk_index,
            "content": c.content,
            **{f"meta_{k}": v for k, v in (c.doc_metadata or {}).items()},
        }
        for c in batch
    ]
    target_store.upsert(
        ids=[c.id for c in batch], vectors=vectors, payloads=payloads
    )
    return len(batch)
