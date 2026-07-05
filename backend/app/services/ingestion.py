"""Ingestion service: tenants, collections, text documents + chunks + embedding + Qdrant."""

import hashlib
import uuid

from sqlalchemy.orm import Session

from app.db.models import Tenant, Collection, Document, Chunk
from app.services.chunking import chunk_document
from app.services.embedder import Embedder
from app.services.usage import record_usage, EMBED_TEXTS
from app.services.vector_store import VectorStore
from app.tracing import span


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def extract_text_from_upload(filename: str, content_type: str, raw: bytes) -> str:
    """Turn uploaded bytes into clean text before ingestion.

    - PDFs (by magic bytes or content-type) are parsed with pypdf so raw binary
      never reaches the DB/chunking/vector pipeline.
    - Everything else is decoded as UTF-8 (lossy).
    - NUL (0x00) and other control chars are stripped: a literal 0x00 byte is
      valid UTF-8 (U+0000) and would otherwise crash the Postgres text insert.
    """
    is_pdf = raw[:5] == b"%PDF-" or (content_type or "").lower() == "application/pdf" \
        or (filename or "").lower().endswith(".pdf")
    if is_pdf:
        import io

        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(raw))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # unreadable/corrupt PDF -> let ING-07 quarantine it
            raise ValueError(f"pdf_parse_failed: {exc}") from exc
    else:
        text = raw.decode("utf-8", errors="replace")

    # Strip NUL + other C0 control chars (keep tab/newline/carriage-return).
    return "".join(
        ch for ch in text if ch == "\t" or ch == "\n" or ch == "\r" or ch >= " "
    )


def create_tenant(db: Session, name: str) -> Tenant:
    tenant = Tenant(name=name)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def list_collections(db: Session, tenant_id: uuid.UUID) -> list[Collection]:
    return (
        db.query(Collection)
        .filter(Collection.tenant_id == tenant_id)
        .order_by(Collection.created_at.asc())
        .all()
    )


def create_collection(
    db: Session,
    tenant_id: uuid.UUID,
    name: str,
    chunking_strategy: str = "fixed",
    chunking_config: dict | None = None,
) -> Collection:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("tenant_not_found")

    collection = Collection(
        tenant_id=tenant_id,
        name=name,
        chunking_strategy=chunking_strategy,
        chunking_config=chunking_config or {},
    )
    db.add(collection)
    db.commit()
    db.refresh(collection)
    return collection


def validate_content(title: str, content: str) -> str | None:
    """Return a failure reason if the document is unparseable/unusable, else None.

    Used to quarantine bad inputs instead of silently dropping them (ING-07).
    """
    if not content or not content.strip():
        return "empty_content"
    # Heuristic: mostly-binary/garbled content has few printable characters.
    printable = sum(1 for ch in content if ch.isprintable() or ch in "\n\r\t")
    if printable / max(len(content), 1) < 0.6:
        return "non_text_content"
    return None


def _resolve_source_uri(source_uri: str | None, title: str) -> str:
    """Stable logical identity for a text source.

    An explicit source_uri (external system id) wins; otherwise we derive a
    deterministic uri from the title so re-uploading the same document maps to
    the same row (enabling ING-04 idempotency and ING-02 delta re-index).
    """
    if source_uri:
        return source_uri
    return f"text://{_sha256(title)}"


def _purge_document_chunks(
    db: Session, document: Document, vector_store: VectorStore
) -> None:
    """Remove a document's chunks from Postgres and its vectors from the store."""
    chunk_ids = [
        row.id
        for row in db.query(Chunk.id).filter(Chunk.document_id == document.id).all()
    ]
    if chunk_ids:
        vector_store.delete(chunk_ids)
        db.query(Chunk).filter(Chunk.document_id == document.id).delete(
            synchronize_session=False
        )


def _invalidate_retrieval_cache(cache, tenant_id, collection_id) -> None:
    """Drop cached retrieval + semantic responses for a changed collection (CACHE-04)."""
    if cache is not None:
        cache.delete_prefix(cache.retrieval_prefix(tenant_id, collection_id))
        cache.delete_prefix(cache.semantic_prefix(tenant_id, collection_id))


def delete_document_by_source_uri(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    source_uri: str,
    vector_store: VectorStore,
    cache=None,
) -> bool:
    """Remove a document (chunks + vectors) identified by its source uri.

    Used to propagate deletions detected by a connector delta (ING-08).
    """
    document = (
        db.query(Document)
        .filter(
            Document.tenant_id == tenant_id,
            Document.collection_id == collection_id,
            Document.source_uri == source_uri,
        )
        .one_or_none()
    )
    if document is None:
        return False
    _purge_document_chunks(db, document, vector_store)
    db.delete(document)
    db.commit()
    _invalidate_retrieval_cache(cache, tenant_id, collection_id)
    return True


def delete_document(
    db: Session,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    vector_store: VectorStore,
    cache=None,
) -> bool:
    """Hard-delete a document, its chunks, and its vectors (ING-08).

    Returns False if the document does not exist or is outside the tenant's
    scope (so callers can surface a 404 without leaking cross-tenant existence).
    """
    document = db.get(Document, document_id)
    if document is None or document.tenant_id != tenant_id:
        return False
    collection_id = document.collection_id
    _purge_document_chunks(db, document, vector_store)
    db.delete(document)
    db.commit()
    _invalidate_retrieval_cache(cache, tenant_id, collection_id)
    return True


def list_documents(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Document]:
    """List documents for a tenant, optionally filtered by collection/status.

    Powers the ingestion-failure dashboard view (ING-07) via status=quarantined.
    """
    q = db.query(Document).filter(Document.tenant_id == tenant_id)
    if collection_id is not None:
        q = q.filter(Document.collection_id == collection_id)
    if status is not None:
        q = q.filter(Document.status == status)
    return q.order_by(Document.created_at.desc()).limit(min(limit, 500)).all()


def erase_document(
    db: Session,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    vector_store: VectorStore,
    cache=None,
) -> dict | None:
    """Right-to-erasure (SEC-06): remove a document, its chunks/vectors, cached
    entries, and any feedback referencing its chunks.

    Returns a summary of what was erased, or None if not found / cross-tenant.
    """
    from sqlalchemy import text as _text

    document = db.get(Document, document_id)
    if document is None or document.tenant_id != tenant_id:
        return None
    collection_id = document.collection_id

    chunk_ids = [
        str(row.id)
        for row in db.query(Chunk.id).filter(Chunk.document_id == document_id).all()
    ]

    feedback_deleted = 0
    if chunk_ids:
        feedback_deleted = db.execute(
            _text(
                "DELETE FROM feedback WHERE tenant_id = :t AND chunk_ids ?| :ids"
            ),
            {"t": str(tenant_id), "ids": chunk_ids},
        ).rowcount

    _purge_document_chunks(db, document, vector_store)
    db.delete(document)
    db.commit()
    _invalidate_retrieval_cache(cache, tenant_id, collection_id)
    return {
        "document_id": str(document_id),
        "chunks_erased": len(chunk_ids),
        "feedback_erased": int(feedback_deleted or 0),
    }


def ingest_text_document(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    title: str,
    content: str,
    embedder: Embedder,
    vector_store: VectorStore,
    source_uri: str | None = None,
    cache=None,
    metadata: dict | None = None,
    source_id: uuid.UUID | None = None,
    ingestion_run_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> tuple[Document, int, bool]:
    """Ingest text idempotently.

    Returns (document, chunks_created, reused). ``reused`` is True when the
    source was already indexed with identical content and nothing was
    re-processed (ING-04). When content changed for a known source, the old
    chunks/vectors are purged and the document is re-indexed in place (ING-02).

    ``metadata`` (source/author/date/classification/ACL tags) is stored on the
    document and every chunk, and mirrored into the vector payload for
    downstream filtered retrieval (ING-06 / RET-04).
    """
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise ValueError("collection_not_found")
    if collection.tenant_id != tenant_id:
        raise ValueError("collection_does_not_belong_to_tenant")

    metadata = metadata or {}
    doc_meta = {"title": title, **metadata}
    content_hash = _sha256(content)
    resolved_uri = _resolve_source_uri(source_uri, title)

    def _set_provenance(doc: Document) -> None:
        # Traceability: this doc came from source X, indexed by run Y, by user Z.
        if source_id is not None:
            doc.source_id = source_id
        if ingestion_run_id is not None:
            doc.ingestion_run_id = ingestion_run_id
        if created_by is not None and doc.created_by is None:
            doc.created_by = created_by

    existing = (
        db.query(Document)
        .filter(
            Document.collection_id == collection_id,
            Document.source_uri == resolved_uri,
        )
        .one_or_none()
    )

    # --- 0. Validate; quarantine bad input instead of dropping it (ING-07) ---
    failure_reason = validate_content(title, content)
    if failure_reason is not None:
        if existing is not None:
            _purge_document_chunks(db, existing, vector_store)
            document = existing
        else:
            document = Document(
                tenant_id=tenant_id,
                collection_id=collection_id,
                source_type="text",
                source_uri=resolved_uri,
            )
            db.add(document)
        document.content_hash = content_hash
        document.status = "quarantined"
        document.doc_metadata = {**doc_meta, "failure_reason": failure_reason}
        _set_provenance(document)
        db.commit()
        db.refresh(document)
        _invalidate_retrieval_cache(cache, tenant_id, collection_id)
        return document, 0, False

    # --- 1a. Idempotent no-op: same source, unchanged content ---
    if existing is not None and existing.content_hash == content_hash:
        # Record that this run touched the doc, even though nothing re-indexed.
        _set_provenance(existing)
        db.commit()
        chunk_count = (
            db.query(Chunk).filter(Chunk.document_id == existing.id).count()
        )
        return existing, chunk_count, True  # no change -> cache stays valid

    # --- 1b. Known source, changed content: purge and re-index in place ---
    if existing is not None:
        _purge_document_chunks(db, existing, vector_store)
        document = existing
        document.content_hash = content_hash
        document.status = "pending"
        document.doc_metadata = doc_meta
        _set_provenance(document)
        db.flush()
    else:
        # --- 1c. New source: create document row (pending) ---
        document = Document(
            tenant_id=tenant_id,
            collection_id=collection_id,
            source_type="text",
            source_uri=resolved_uri,
            content_hash=content_hash,
            status="pending",
            doc_metadata=doc_meta,
        )
        _set_provenance(document)
        db.add(document)
        db.flush()

    # --- 2. Chunk (per-collection strategy, ING-05) and persist to Postgres ---
    pieces = chunk_document(
        content,
        strategy=collection.chunking_strategy,
        config=collection.chunking_config,
    )
    chunk_rows: list[Chunk] = []
    for index, piece in enumerate(pieces):
        chunk = Chunk(
            tenant_id=tenant_id,
            collection_id=collection_id,
            document_id=document.id,
            chunk_index=index,
            content=piece,
            content_hash=_sha256(piece),
            doc_metadata=dict(metadata),
        )
        db.add(chunk)
        chunk_rows.append(chunk)

    document.status = "chunked"
    db.flush()  # flush so chunk rows get their IDs

    # --- 3. Embed ---
    texts = [c.content for c in chunk_rows]
    with span("ingestion.embed"):
        vectors = embedder.embed(texts)
    record_usage(db, tenant_id, collection_id, EMBED_TEXTS, len(texts))  # MON-08

    # --- 4. Upsert to Qdrant ---
    chunk_ids = [c.id for c in chunk_rows]
    payloads = [
        {
            "tenant_id": str(c.tenant_id),
            "collection_id": str(c.collection_id),
            "document_id": str(c.document_id),
            "chunk_index": c.chunk_index,
            "content": c.content,
            # Flattened metadata for exact-match filtered retrieval (ING-06/RET-04)
            **{f"meta_{key}": value for key, value in metadata.items()},
        }
        for c in chunk_rows
    ]
    vector_store.upsert(ids=chunk_ids, vectors=vectors, payloads=payloads)

    # --- 5. Mark as embedded and commit ---
    document.status = "embedded"
    db.commit()
    db.refresh(document)
    _invalidate_retrieval_cache(cache, tenant_id, collection_id)  # CACHE-04
    return document, len(pieces), False
