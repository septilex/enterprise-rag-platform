"""Ingestion service: tenants, collections, text documents + chunks + embedding + Qdrant."""

import hashlib
import uuid

from sqlalchemy.orm import Session

from app.db.models import Tenant, Collection, Document, Chunk
from app.services.chunking import chunk_text
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_tenant(db: Session, name: str) -> Tenant:
    tenant = Tenant(name=name)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def create_collection(db: Session, tenant_id: uuid.UUID, name: str) -> Collection:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("tenant_not_found")

    collection = Collection(tenant_id=tenant_id, name=name)
    db.add(collection)
    db.commit()
    db.refresh(collection)
    return collection


def ingest_text_document(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    title: str,
    content: str,
    embedder: Embedder,
    vector_store: VectorStore,
) -> tuple[Document, int]:
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise ValueError("collection_not_found")
    if collection.tenant_id != tenant_id:
        raise ValueError("collection_does_not_belong_to_tenant")

    # --- 1. Create document row (pending) ---
    document = Document(
        tenant_id=tenant_id,
        collection_id=collection_id,
        source_type="text",
        source_uri=f"text://{uuid.uuid4()}",
        content_hash=_sha256(content),
        status="pending",
        doc_metadata={"title": title},
    )
    db.add(document)
    db.flush()

    # --- 2. Chunk and persist to Postgres ---
    pieces = chunk_text(content, chunk_size=800, overlap=100)
    chunk_rows: list[Chunk] = []
    for index, piece in enumerate(pieces):
        chunk = Chunk(
            tenant_id=tenant_id,
            collection_id=collection_id,
            document_id=document.id,
            chunk_index=index,
            content=piece,
            content_hash=_sha256(piece),
            doc_metadata={},
        )
        db.add(chunk)
        chunk_rows.append(chunk)

    document.status = "chunked"
    db.flush()  # flush so chunk rows get their IDs

    # --- 3. Embed ---
    texts = [c.content for c in chunk_rows]
    vectors = embedder.embed(texts)

    # --- 4. Upsert to Qdrant ---
    chunk_ids = [c.id for c in chunk_rows]
    payloads = [
        {
            "tenant_id": str(c.tenant_id),
            "collection_id": str(c.collection_id),
            "document_id": str(c.document_id),
            "chunk_index": c.chunk_index,
            "content": c.content,
        }
        for c in chunk_rows
    ]
    vector_store.upsert(ids=chunk_ids, vectors=vectors, payloads=payloads)

    # --- 5. Mark as embedded and commit ---
    document.status = "embedded"
    db.commit()
    db.refresh(document)
    return document, len(pieces)
