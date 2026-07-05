"""Ingestion orchestration: sources + ingestion runs (ING-01/02/07/09).

Every ingest action (manual upload, api text, webhook, connector, reindex)
flows through :func:`ingest_items`, which creates an IngestionRun, drives the
idempotent core (:func:`app.services.ingestion.ingest_text_document`) with
provenance, tallies results, and finalizes run + source status. Manual upload is
just one ``source_type`` — no special-case code path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, IngestionRun, Source
from app.observability import record_ingestion_run
from app.services import ingestion
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore

# Source types / trigger types (future-safe string enums).
SOURCE_MANUAL_UPLOAD = "manual_upload"
SOURCE_API_TEXT = "api_text"
SOURCE_WEBHOOK = "webhook"

TRIGGER_MANUAL = "manual"
TRIGGER_WEBHOOK = "webhook"
TRIGGER_SCHEDULED = "scheduled"
TRIGGER_REINDEX = "reindex"
TRIGGER_SYSTEM = "system"

# Run statuses.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_source(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None,
    source_type: str,
    display_name: str,
    created_by: uuid.UUID | None = None,
    external_ref: str | None = None,
    config: dict | None = None,
) -> Source:
    """Idempotent per (collection, source_type, display_name)."""
    src = (
        db.query(Source)
        .filter(
            Source.collection_id == collection_id,
            Source.source_type == source_type,
            Source.display_name == display_name,
        )
        .one_or_none()
    )
    if src is None:
        src = Source(
            tenant_id=tenant_id,
            collection_id=collection_id,
            source_type=source_type,
            display_name=display_name,
            created_by=created_by,
            external_ref=external_ref,
            config=config or {},
        )
        db.add(src)
        db.commit()
        db.refresh(src)
    return src


def manual_upload_source(
    db: Session, tenant_id: uuid.UUID, collection_id: uuid.UUID,
    created_by: uuid.UUID | None = None,
) -> Source:
    return get_or_create_source(
        db, tenant_id, collection_id, SOURCE_MANUAL_UPLOAD, "Manual uploads",
        created_by=created_by,
    )


def list_sources(
    db: Session, tenant_id: uuid.UUID, collection_id: uuid.UUID | None = None
) -> list[Source]:
    q = db.query(Source).filter(Source.tenant_id == tenant_id)
    if collection_id is not None:
        q = q.filter(Source.collection_id == collection_id)
    return q.order_by(Source.created_at.asc()).all()


def get_source(db: Session, tenant_id: uuid.UUID, source_id: uuid.UUID) -> Source | None:
    src = db.get(Source, source_id)
    if src is None or src.tenant_id != tenant_id:
        return None
    return src


def list_runs(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[IngestionRun]:
    q = db.query(IngestionRun).filter(IngestionRun.tenant_id == tenant_id)
    if collection_id is not None:
        q = q.filter(IngestionRun.collection_id == collection_id)
    if source_id is not None:
        q = q.filter(IngestionRun.source_id == source_id)
    return q.order_by(IngestionRun.created_at.desc()).limit(min(limit, 500)).all()


def active_run_for_source(db: Session, source_id: uuid.UUID) -> IngestionRun | None:
    """Return an in-flight (queued/running) run for a source, if any.

    Used to guard against duplicate ingestion runs (idempotent rerun).
    """
    return (
        db.query(IngestionRun)
        .filter(
            IngestionRun.source_id == source_id,
            IngestionRun.status.in_([STATUS_QUEUED, STATUS_RUNNING]),
        )
        .order_by(IngestionRun.created_at.desc())
        .first()
    )


def recover_stuck_runs(db: Session, timeout_seconds: int = 900) -> int:
    """Fail runs stuck in queued/running past the timeout (worker crash recovery).

    Safe to call at API/worker startup: a job whose worker died mid-run no
    longer hangs forever; the run is marked failed so operators can retry it.
    """
    from datetime import timedelta

    cutoff = _now() - timedelta(seconds=timeout_seconds)
    stuck = (
        db.query(IngestionRun)
        .filter(
            IngestionRun.status.in_([STATUS_QUEUED, STATUS_RUNNING]),
            IngestionRun.created_at < cutoff,
        )
        .all()
    )
    for run in stuck:
        run.status = STATUS_FAILED
        run.error_summary = "recovered: run exceeded timeout (worker likely crashed)"
        run.completed_at = _now()
    if stuck:
        db.commit()
    return len(stuck)


def create_queued_run(
    db: Session, source: Source, trigger_type: str = TRIGGER_SCHEDULED,
    triggered_by: uuid.UUID | None = None,
) -> IngestionRun:
    """Create a run in `queued` state (worker will transition it to running)."""
    run = IngestionRun(
        tenant_id=source.tenant_id, source_id=source.id,
        collection_id=source.collection_id, triggered_by=triggered_by,
        trigger_type=trigger_type, status=STATUS_QUEUED,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def sync_source(
    db: Session,
    source: Source,
    embedder: Embedder,
    vector_store: VectorStore,
    cache=None,
    triggered_by: uuid.UUID | None = None,
    trigger_type: str = TRIGGER_SCHEDULED,
    run: IngestionRun | None = None,
) -> IngestionRun:
    """Pull changed documents from a connector-backed source and ingest them.

    Delta-aware: uses the connector's cursor stored in ``source.config`` so only
    new/changed documents are re-ingested (ING-02). Updates the cursor after a
    successful run. Runs the shared ingest path (same as manual upload).
    """
    from app.services import connectors

    if source.config.get("connector_type") is None:
        raise ValueError("source has no connector_type; not a connector source")

    connector = connectors.build_connector(
        source.config["connector_type"], source.config)
    result = connector.fetch_delta(source.config.get("cursor"))
    items = [
        {"title": d.title, "content": d.content, "source_uri": d.source_uri,
         "metadata": {**d.metadata, "connector": source.config["connector_type"]}}
        for d in result.documents
    ]
    run, _ = ingest_items(
        db, source.tenant_id, source.collection_id, source, items,
        embedder=embedder, vector_store=vector_store,
        trigger_type=trigger_type, triggered_by=triggered_by, cache=cache,
        run_metadata={"connector_type": source.config["connector_type"],
                      "delta_documents": len(items),
                      "delta_deletions": len(result.deleted)},
        run=run,
    )

    # Propagate deletions detected by the connector delta (ING-08).
    deleted = 0
    for uri in result.deleted:
        if ingestion.delete_document_by_source_uri(
            db, source.tenant_id, source.collection_id, uri, vector_store, cache):
            deleted += 1
    if deleted and run.status != STATUS_FAILED:
        run.documents_deleted = deleted
        db.commit()
        db.refresh(run)

    # Persist the new cursor so the next sync only sees further changes.
    if run.status != STATUS_FAILED:
        cfg = dict(source.config)
        cfg["cursor"] = result.cursor
        source.config = cfg
        db.commit()
    return run


def set_source_enabled(db: Session, source: Source, enabled: bool) -> Source:
    source.enabled = enabled
    db.commit()
    db.refresh(source)
    return source


def reindex_source(
    db: Session,
    source: Source,
    embedder: Embedder,
    vector_store: VectorStore,
    triggered_by: uuid.UUID | None = None,
) -> IngestionRun:
    """Re-embed the source's existing chunks into the vector store (ING-10 hook).

    Safe re-embedding: uses stored chunk content (no re-chunk), re-upserts to the
    vector store, and records a `reindex`-trigger run. Useful after an embedding
    model change or to repair a vector index.
    """
    run = IngestionRun(
        tenant_id=source.tenant_id, source_id=source.id,
        collection_id=source.collection_id, triggered_by=triggered_by,
        trigger_type=TRIGGER_REINDEX, status=STATUS_RUNNING, started_at=_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    docs = db.query(Document).filter(Document.source_id == source.id).all()
    reembedded = 0
    chunks_total = 0
    try:
        for doc in docs:
            chunk_rows = (
                db.query(Chunk).filter(Chunk.document_id == doc.id)
                .order_by(Chunk.chunk_index).all()
            )
            if not chunk_rows:
                continue
            vectors = embedder.embed([c.content for c in chunk_rows])
            payloads = [
                {
                    "tenant_id": str(c.tenant_id),
                    "collection_id": str(c.collection_id),
                    "document_id": str(c.document_id),
                    "chunk_index": c.chunk_index,
                    "content": c.content,
                    **{f"meta_{k}": v for k, v in (c.doc_metadata or {}).items()},
                }
                for c in chunk_rows
            ]
            vector_store.upsert(
                ids=[c.id for c in chunk_rows], vectors=vectors, payloads=payloads)
            doc.ingestion_run_id = run.id
            reembedded += 1
            chunks_total += len(chunk_rows)
    except Exception as exc:
        run.status = STATUS_FAILED
        run.error_summary = str(exc)[:1000]
        run.completed_at = _now()
        source.last_error_at = _now()
        db.commit()
        db.refresh(run)
        raise

    run.status = STATUS_SUCCEEDED
    run.documents_seen = len(docs)
    run.documents_indexed = reembedded
    run.chunks_created = chunks_total
    run.completed_at = _now()
    source.last_success_at = _now()
    db.commit()
    db.refresh(run)
    return run


def ingest_items(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    source: Source,
    items: list[dict],
    embedder: Embedder,
    vector_store: VectorStore,
    trigger_type: str = TRIGGER_MANUAL,
    triggered_by: uuid.UUID | None = None,
    cache=None,
    run_metadata: dict | None = None,
    run: IngestionRun | None = None,
) -> tuple[IngestionRun, list[tuple]]:
    """Run an ingestion attempt over ``items`` and record it as an IngestionRun.

    Each item: {title, content, source_uri?, metadata?}. Returns
    (run, results) where results is a list of (document, chunk_count, reused).
    A pre-created queued ``run`` (from the worker) is transitioned to running;
    otherwise a new run is created. Idempotency / delta / quarantine semantics
    come from the core pipeline; we track provenance + roll counts up (ING-02/04/07).
    """
    if run is None:
        run = IngestionRun(
            tenant_id=tenant_id,
            source_id=source.id,
            collection_id=collection_id,
            triggered_by=triggered_by,
            trigger_type=trigger_type,
            status=STATUS_RUNNING,
            started_at=_now(),
            run_metadata=run_metadata or {},
        )
        db.add(run)
    else:
        run.status = STATUS_RUNNING
        run.started_at = _now()
    db.commit()
    db.refresh(run)

    seen = indexed = quarantined = chunks_created = chunks_reused = 0
    results: list[tuple] = []
    try:
        for item in items:
            seen += 1
            document, count, reused = ingestion.ingest_text_document(
                db,
                tenant_id=tenant_id,
                collection_id=collection_id,
                title=item["title"],
                content=item["content"],
                embedder=embedder,
                vector_store=vector_store,
                source_uri=item.get("source_uri"),
                cache=cache,
                metadata=item.get("metadata"),
                source_id=source.id,
                ingestion_run_id=run.id,
                created_by=triggered_by,
            )
            results.append((document, count, reused))
            if document.status == "quarantined":
                quarantined += 1
            elif reused:
                indexed += 1
                chunks_reused += count
            else:
                indexed += 1
                chunks_created += count
    except Exception as exc:  # a hard failure fails the whole run
        run.status = STATUS_FAILED
        run.error_summary = str(exc)[:1000]
        run.documents_seen = seen
        run.documents_indexed = indexed
        run.documents_quarantined = quarantined
        run.completed_at = _now()
        source.last_error_at = _now()
        db.commit()
        db.refresh(run)
        record_ingestion_run(STATUS_FAILED, run.trigger_type)
        raise

    # Roll up final status.
    if quarantined and indexed == 0:
        status = STATUS_FAILED
    elif quarantined:
        status = STATUS_PARTIAL
    else:
        status = STATUS_SUCCEEDED

    run.status = status
    run.documents_seen = seen
    run.documents_indexed = indexed
    run.documents_quarantined = quarantined
    run.chunks_created = chunks_created
    run.chunks_reused = chunks_reused
    if quarantined:
        run.error_summary = f"{quarantined} document(s) quarantined"
    run.completed_at = _now()
    if status == STATUS_FAILED:
        source.last_error_at = _now()
    else:
        source.last_success_at = _now()
    db.commit()
    db.refresh(run)
    record_ingestion_run(status, run.trigger_type)
    return run, results
