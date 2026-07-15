import json
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_api_key, get_principal, Principal
from app.db.base import get_db
from app.schemas import (
    TenantCreate,
    TenantResponse,
    CollectionCreate,
    CollectionResponse,
    DocumentTextIngestRequest,
    DocumentIngestResponse,
    DocumentDeleteResponse,
    DocumentSummary,
    SearchRequest,
    SearchResultItem,
    SearchResponse,
    ChatRequest,
    ChatResponse,
    Citation,
    FeedbackCreate,
    FeedbackResponse,
    ScorecardRequest,
    ScorecardResponse,
)
from app.schemas import (
    WebhookIngestRequest,
    WebhookIngestResponse,
    ChatSessionCreate,
    ChatSessionResponse,
    ChatMessageResponse,
    DriftCheckRequest,
)
from app.db.models import Feedback, Chunk, IngestionRun
from sqlalchemy import func
from app.schemas import (
    MeResponse, TenantRole, BootstrapRequest, MembershipCreate,
    MemberResponse, AuditEntry, SourceResponse, IngestionRunResponse, SourceUpdate,
    SourceCreate, SyncResponse, SystemStatus, WorkerStatus, QueueStatus, SourceHealth,
)
from app.services import identity, ingestion_runs
from app.services import chat_sessions, drift
from app.services.evaluation import run_scorecard
from app.services import connectors
from app.services.usage import cost_report
from app.services import ingestion, retrieval
from app.services.retrieval import search_debug
from app.services.embedder import OpenAIEmbedder
from app.services.generation import generate_answer, stream_answer
from app.services.llm import OpenAILLM
from app.services.vector_store import QdrantVectorStore
from app.observability import log_query, audit_log, record_query, current_request_id
from app.services import llm_observability
from app.tracing import span
from app.services.cache import build_cache
from app.services.embedder import CachedEmbedder
from app.core.config import settings

# All API endpoints require a valid API key when keys are configured (SEC-01).
router = APIRouter(dependencies=[Depends(require_api_key)])

_cache = build_cache()
_raw_embedder = OpenAIEmbedder()
# Transparent embedding cache (CACHE-01) when caching is enabled.
_embedder = (
    CachedEmbedder(_raw_embedder, _cache, _raw_embedder.model, settings.EMBED_CACHE_TTL)
    if _cache is not None
    else _raw_embedder
)
_vector_store = QdrantVectorStore()
_llm = OpenAILLM()


def _actor(principal: Principal) -> str:
    return principal.email or ("superuser" if principal.superuser else "unknown")


def _audit(db, tenant_id, principal, action, target):
    """Emit the audit log line AND persist an immutable audit row (SEC-05)."""
    actor = _actor(principal)
    audit_log(action, actor, target)              # structured log line
    try:
        identity.record_audit(db, tenant_id, actor, action, target)
    except Exception:                             # never break the request path
        db.rollback()


def _build_job_queue():
    """Redis-backed ingestion queue when Redis is configured (ING-09)."""
    if not settings.CACHE_ENABLED:
        return None
    import redis

    from app.services.jobs import RedisJobQueue

    return RedisJobQueue(redis.Redis.from_url(settings.REDIS_URL, decode_responses=True))


_job_queue = _build_job_queue()


@router.get("/me", response_model=MeResponse, tags=["identity"])
def me(principal: Principal = Depends(get_principal)):
    """Current caller identity + tenant roles (SEC-01/02)."""
    return MeResponse(
        authenticated=True,
        superuser=principal.superuser,
        user_id=principal.user_id,
        email=principal.email,
        tenants=[TenantRole(tenant_id=t, role=r) for t, r in principal.memberships.items()],
    )


@router.post("/admin/bootstrap", tags=["admin"])
def admin_bootstrap(
    payload: BootstrapRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Idempotently create a tenant + admin user (dev bootstrap for AUTH_MODE=dev).

    Allowed for a superuser (open mode) or an existing global superuser.
    """
    principal.require_admin()
    return identity.bootstrap_admin(db, payload.email, payload.tenant_name)


@router.get("/admin/members", response_model=list[MemberResponse], tags=["admin"])
def list_members(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """List members + roles for a tenant (tenant admin only, SEC-02)."""
    principal.require_role(tenant_id, "admin")
    return [
        MemberResponse(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role)
        for (u, m) in identity.list_members(db, tenant_id)
    ]


@router.post("/admin/members", response_model=MemberResponse, status_code=201, tags=["admin"])
def grant_membership(
    payload: MembershipCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Grant/update a user's role in a tenant (tenant admin only, SEC-02)."""
    principal.require_role(payload.tenant_id, "admin")
    user = identity.get_or_create_user(db, payload.email)
    m = identity.set_membership(db, user.id, payload.tenant_id, payload.role)
    _audit(db, payload.tenant_id, principal, "membership.grant",
           {"email": payload.email, "role": payload.role})
    return MemberResponse(
        user_id=user.id, email=user.email, display_name=user.display_name, role=m.role,
    )


@router.delete("/admin/members", tags=["admin"])
def remove_membership(
    tenant_id: uuid.UUID,
    email: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Revoke a user's membership (tenant admin only, SEC-02).

    Refuses to remove the last admin so a workspace can't be orphaned.
    """
    principal.require_role(tenant_id, "admin")
    members = {u.email: m.role for (u, m) in identity.list_members(db, tenant_id)}
    if members.get(email) == "admin" and identity.count_admins(db, tenant_id) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last admin")
    if not identity.remove_membership(db, tenant_id, email):
        raise HTTPException(status_code=404, detail="Member not found")
    _audit(db, tenant_id, principal, "membership.revoke", {"email": email})
    return {"removed": True, "email": email}


@router.get("/admin/audit", response_model=list[AuditEntry], tags=["admin"])
def list_audit(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Immutable audit trail for a tenant (tenant admin only, SEC-05)."""
    principal.require_role(tenant_id, "admin")
    return identity.list_audit(db, tenant_id)


@router.post("/tenants", response_model=TenantResponse, status_code=201, tags=["tenants"])
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.require_admin()  # tenant creation is a global-admin action (SEC-02)
    try:
        tenant = ingestion.create_tenant(db, name=payload.name)
        _audit(db, tenant.id, principal, "tenant.create", {"name": tenant.name})
        return tenant
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tenant name already exists")


@router.post("/collections", response_model=CollectionResponse, status_code=201, tags=["collections"])
def create_collection(
    payload: CollectionCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.authorize(payload.tenant_id)
    principal.require_role(payload.tenant_id, "editor")
    try:
        collection = ingestion.create_collection(
            db,
            tenant_id=payload.tenant_id,
            name=payload.name,
            chunking_strategy=payload.chunking_strategy,
            chunking_config=payload.chunking_config,
        )
        _audit(db, payload.tenant_id, principal, "collection.create",
               {"collection_id": str(collection.id), "name": collection.name})
        return collection
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Collection name already exists for this tenant")


@router.get("/collections", response_model=list[CollectionResponse], tags=["collections"])
def list_collections(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.authorize(tenant_id)
    return ingestion.list_collections(db, tenant_id=tenant_id)


@router.post("/ingest/batch", tags=["documents"])
def ingest_batch(
    payload: WebhookIngestRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Enqueue a bulk backfill onto the bulk lane (ING-09).

    Requires the ``text_batch`` connector config (list of documents). Jobs are
    drained by separate worker processes so incremental updates aren't blocked.
    Falls back to inline ingestion when no queue is configured.
    """
    principal.authorize(payload.tenant_id, payload.collection_id)
    principal.require_role(payload.tenant_id, "editor")
    docs = payload.config.get("documents", [])
    if _job_queue is None:
        connector = connectors.get_connector("text_batch", documents=docs)
        summary = connectors.run_connector(
            db, connector, payload.tenant_id, payload.collection_id,
            _embedder, _vector_store, cache=_cache)
        return {"mode": "inline", **summary}
    for d in docs:
        _job_queue.enqueue({
            "tenant_id": str(payload.tenant_id),
            "collection_id": str(payload.collection_id),
            "title": d["title"], "content": d["content"],
            "source_uri": d.get("source_uri"), "metadata": d.get("metadata"),
        }, bulk=True)
    return {"mode": "queued", "enqueued": len(docs), "depth": _job_queue.depth()}


@router.get("/ingest/queue/depth", tags=["documents"])
def ingest_queue_depth(principal: Principal = Depends(get_principal)):
    """Current ingestion queue depth per lane (ING-09 / MON queue depth)."""
    principal.require_admin()
    return _job_queue.depth() if _job_queue is not None else {"incremental": 0, "bulk": 0}


@router.post(
    "/ingest/webhook", response_model=WebhookIngestResponse, tags=["documents"]
)
def ingest_webhook(
    payload: WebhookIngestRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Event-driven ingestion trigger (ING-03).

    Runs a registered connector on demand so an update is reflected in the
    index immediately, without waiting for the next scheduled poll.
    """
    principal.authorize(payload.tenant_id, payload.collection_id)
    principal.require_role(payload.tenant_id, "editor")
    try:
        connector = connectors.get_connector(payload.source_type, **payload.config)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # A connector run is an ingestion run against a connector-typed source.
    source = ingestion_runs.get_or_create_source(
        db, payload.tenant_id, payload.collection_id,
        payload.source_type, payload.source_type, created_by=principal.user_id,
        config=payload.config)
    items = [
        {"title": d.title, "content": d.content, "source_uri": d.source_uri}
        for d in connector.fetch()
    ]
    run, _ = ingestion_runs.ingest_items(
        db, payload.tenant_id, payload.collection_id, source, items,
        embedder=_embedder, vector_store=_vector_store,
        trigger_type=ingestion_runs.TRIGGER_WEBHOOK,
        triggered_by=principal.user_id, cache=_cache)

    _audit(db, payload.tenant_id, principal, "ingest.webhook",
           {"collection_id": str(payload.collection_id),
            "source_type": payload.source_type, "run_id": str(run.id),
            "run_status": run.status})
    return WebhookIngestResponse(
        source_type=payload.source_type, seen=run.documents_seen,
        ingested=run.documents_indexed, reused=0)


@router.post("/documents/text", response_model=DocumentIngestResponse, status_code=201, tags=["documents"])
def ingest_text_document(
    payload: DocumentTextIngestRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.authorize(payload.tenant_id, payload.collection_id)
    principal.require_role(payload.tenant_id, "editor")
    source = ingestion_runs.get_or_create_source(
        db, payload.tenant_id, payload.collection_id,
        ingestion_runs.SOURCE_API_TEXT, "API text", created_by=principal.user_id)
    try:
        run, results = ingestion_runs.ingest_items(
            db, payload.tenant_id, payload.collection_id, source,
            items=[{"title": payload.title, "content": payload.content,
                    "source_uri": payload.source_uri, "metadata": payload.metadata}],
            embedder=_embedder, vector_store=_vector_store,
            trigger_type=ingestion_runs.TRIGGER_MANUAL,
            triggered_by=principal.user_id, cache=_cache,
        )
        document, count, reused = results[0]
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    return DocumentIngestResponse(
        document_id=document.id,
        status=document.status,
        chunks_created=count,
        reused=reused,
    )


@router.post(
    "/documents/upload", response_model=DocumentIngestResponse,
    status_code=201, tags=["documents"],
)
def upload_document(
    tenant_id: uuid.UUID = Form(...),
    collection_id: uuid.UUID = Form(...),
    session_id: uuid.UUID | None = Form(default=None),
    background: bool = Form(default=False),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """UI-07: upload a file as retrieval context for a tenant/collection.

    The file is decoded as text and run through the idempotent ingest pipeline.
    It is tagged with upload/session metadata so it can be scoped or filtered.
    Non-text/corrupt files are quarantined (ING-07), not silently dropped.

    Large files (> UPLOAD_BACKGROUND_THRESHOLD_BYTES) — or any upload with
    background=true — are spooled to disk and ingested by the worker on the
    bulk lane (ING-09), so the request returns immediately with a run_id to
    poll instead of blocking on parse+embed.
    """
    principal.authorize(tenant_id, collection_id)
    principal.require_role(tenant_id, "editor")
    raw = file.file.read()
    metadata = {"uploaded": True, "filename": file.filename or "upload"}
    if session_id is not None:
        metadata["session_id"] = str(session_id)

    go_background = _job_queue is not None and (
        background or len(raw) > settings.UPLOAD_BACKGROUND_THRESHOLD_BYTES
    )
    if go_background:
        filename = file.filename or f"upload-{uuid.uuid4()}"
        spool_dir = Path(settings.UPLOAD_SPOOL_DIR)
        spool_dir.mkdir(parents=True, exist_ok=True)
        spool_path = spool_dir / f"{uuid.uuid4().hex}_{Path(filename).name}"
        spool_path.write_bytes(raw)

        source = ingestion_runs.manual_upload_source(
            db, tenant_id, collection_id, created_by=principal.user_id)
        run = ingestion_runs.create_queued_run(
            db, source, trigger_type=ingestion_runs.TRIGGER_MANUAL,
            triggered_by=principal.user_id)
        _job_queue.enqueue({
            "kind": "ingest_upload",
            "run_id": str(run.id), "source_id": str(source.id),
            "tenant_id": str(tenant_id), "collection_id": str(collection_id),
            "spool_path": str(spool_path), "filename": filename,
            "content_type": file.content_type or "", "metadata": metadata,
            "triggered_by": str(principal.user_id) if principal.user_id else None,
            "attempt": 0,
        }, bulk=True)
        _audit(db, tenant_id, principal, "document.upload",
               {"collection_id": str(collection_id), "filename": filename,
                "run_id": str(run.id), "mode": "background",
                "bytes": len(raw)})
        return DocumentIngestResponse(
            document_id=None, status="queued", chunks_created=0,
            run_id=run.id, background=True,
        )

    try:
        text = ingestion.extract_text_from_upload(
            file.filename or "", file.content_type or "", raw
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Manual upload is one source_type routed through the ingestion framework.
    source = ingestion_runs.manual_upload_source(
        db, tenant_id, collection_id, created_by=principal.user_id)
    filename = file.filename or f"upload-{uuid.uuid4()}"
    run, results = ingestion_runs.ingest_items(
        db, tenant_id, collection_id, source,
        items=[{"title": filename, "content": text,
                "source_uri": f"upload://{filename}", "metadata": metadata}],
        embedder=_embedder, vector_store=_vector_store,
        trigger_type=ingestion_runs.TRIGGER_MANUAL,
        triggered_by=principal.user_id, cache=_cache,
        run_metadata={"filename": filename, "content_type": file.content_type},
    )
    document, count, reused = results[0]

    _audit(db, tenant_id, principal, "document.upload",
           {"collection_id": str(collection_id), "document_id": str(document.id),
            "filename": file.filename, "run_id": str(run.id), "run_status": run.status})
    return DocumentIngestResponse(
        document_id=document.id, status=document.status,
        chunks_created=count, reused=reused,
    )


@router.post("/documents/{document_id}/erase", tags=["governance"])
def erase_document(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Right-to-erasure: purge a document and all references (SEC-06)."""
    principal.authorize(tenant_id)
    principal.require_role(tenant_id, "admin")
    result = ingestion.erase_document(
        db, tenant_id=tenant_id, document_id=document_id,
        vector_store=_vector_store, cache=_cache,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    _audit(db, tenant_id, principal, "document.erase", result)
    return result


@router.get("/sources", response_model=list[SourceResponse], tags=["ingestion"])
def list_sources(
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """List ingestion sources for a tenant/collection (ING-01 visibility)."""
    principal.authorize(tenant_id, collection_id)
    return ingestion_runs.list_sources(db, tenant_id, collection_id)


@router.post("/sources", response_model=SourceResponse, status_code=201, tags=["ingestion"])
def create_source(
    payload: SourceCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Register a connector-backed source (editor+).

    `source_type` is a registered connector (e.g. `filesystem`, `s3_mock`).
    `config` holds the connector's constructor kwargs; it is validated by
    instantiating the connector once.
    """
    principal.authorize(payload.tenant_id, payload.collection_id)
    principal.require_role(payload.tenant_id, "editor")
    try:
        connectors.build_connector(payload.source_type, payload.config)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid connector: {exc}")
    src = ingestion_runs.get_or_create_source(
        db, payload.tenant_id, payload.collection_id, payload.source_type,
        payload.display_name, created_by=principal.user_id,
        config={"connector_type": payload.source_type, **payload.config})
    _audit(db, payload.tenant_id, principal, "source.create",
           {"source_id": str(src.id), "source_type": payload.source_type})
    return src


@router.post("/sources/{source_id}/sync", response_model=SyncResponse, tags=["ingestion"])
def sync_source(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Trigger a connector sync (ING-03). Enqueued to a worker when a queue is
    configured, else run inline. Delta-aware via the connector cursor."""
    src = ingestion_runs.get_source(db, tenant_id, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    principal.require_role(tenant_id, "editor")
    if not src.config.get("connector_type"):
        raise HTTPException(status_code=400, detail="Source is not a connector source")

    # Idempotent rerun guard: if a sync is already queued/running for this
    # source, return it instead of creating a duplicate run.
    active = ingestion_runs.active_run_for_source(db, src.id)
    if active is not None:
        return SyncResponse(run_id=active.id, status=active.status, mode="already_active")

    if _job_queue is not None:
        run = ingestion_runs.create_queued_run(
            db, src, trigger_type=ingestion_runs.TRIGGER_MANUAL,
            triggered_by=principal.user_id)
        _job_queue.enqueue({
            "kind": "sync_source", "source_id": str(src.id), "run_id": str(run.id),
            "trigger_type": ingestion_runs.TRIGGER_MANUAL,
            "triggered_by": str(principal.user_id) if principal.user_id else None,
            "attempt": 0,
        })
        _audit(db, tenant_id, principal, "source.sync",
               {"source_id": str(src.id), "run_id": str(run.id), "mode": "queued"})
        return SyncResponse(run_id=run.id, status=run.status, mode="queued")

    run = ingestion_runs.sync_source(
        db, src, _embedder, _vector_store, cache=_cache,
        triggered_by=principal.user_id, trigger_type=ingestion_runs.TRIGGER_MANUAL)
    _audit(db, tenant_id, principal, "source.sync",
           {"source_id": str(src.id), "run_id": str(run.id), "mode": "inline"})
    return SyncResponse(run_id=run.id, status=run.status, mode="inline")


@router.get("/sources/{source_id}", response_model=SourceResponse, tags=["ingestion"])
def get_source(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.authorize(tenant_id)
    src = ingestion_runs.get_source(db, tenant_id, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return src


@router.patch("/sources/{source_id}", response_model=SourceResponse, tags=["ingestion"])
def update_source(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    payload: SourceUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Enable/disable a source (editor+)."""
    src = ingestion_runs.get_source(db, tenant_id, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    principal.require_role(tenant_id, "editor")
    ingestion_runs.set_source_enabled(db, src, payload.enabled)
    _audit(db, tenant_id, principal, "source.update",
           {"source_id": str(source_id), "enabled": payload.enabled})
    return src


@router.post("/sources/{source_id}/reindex", response_model=IngestionRunResponse, tags=["ingestion"])
def reindex_source(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Re-embed a source's documents into the vector store as a reindex run (ING-10)."""
    src = ingestion_runs.get_source(db, tenant_id, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    principal.require_role(tenant_id, "editor")
    run = ingestion_runs.reindex_source(
        db, src, _embedder, _vector_store, triggered_by=principal.user_id)
    _audit(db, tenant_id, principal, "source.reindex",
           {"source_id": str(source_id), "run_id": str(run.id),
            "documents_indexed": run.documents_indexed})
    return run


@router.get("/ingestion/runs", response_model=list[IngestionRunResponse], tags=["ingestion"])
def list_ingestion_runs(
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Ingestion run history with status/counts/errors (operator visibility)."""
    principal.authorize(tenant_id, collection_id)
    return ingestion_runs.list_runs(
        db, tenant_id, collection_id=collection_id, source_id=source_id, limit=limit)


@router.post("/ingestion/runs/{run_id}/retry", response_model=SyncResponse, tags=["ingestion"])
def retry_ingestion_run(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Re-run a failed ingestion run against its source (editor+).

    Recovers a failed job cleanly: creates a fresh run (delta-aware, so already
    indexed docs are skipped) rather than mutating the failed record.
    """
    from app.db.models import IngestionRun as _Run

    run = db.get(_Run, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Run not found")
    principal.require_role(tenant_id, "editor")
    if run.status not in ("failed", "partial"):
        raise HTTPException(status_code=400, detail="Only failed/partial runs can be retried")
    src = ingestion_runs.get_source(db, tenant_id, run.source_id)
    if src is None or not src.config.get("connector_type"):
        raise HTTPException(status_code=400, detail="Run's source is not retryable (not a connector source)")

    active = ingestion_runs.active_run_for_source(db, src.id)
    if active is not None:
        return SyncResponse(run_id=active.id, status=active.status, mode="already_active")

    new_run = ingestion_runs.create_queued_run(
        db, src, trigger_type=ingestion_runs.TRIGGER_REINDEX, triggered_by=principal.user_id)
    _audit(db, tenant_id, principal, "ingestion.retry",
           {"failed_run_id": str(run_id), "new_run_id": str(new_run.id)})
    if _job_queue is not None:
        _job_queue.enqueue({
            "kind": "sync_source", "source_id": str(src.id), "run_id": str(new_run.id),
            "trigger_type": ingestion_runs.TRIGGER_REINDEX,
            "triggered_by": str(principal.user_id) if principal.user_id else None,
            "attempt": 0})
        return SyncResponse(run_id=new_run.id, status=new_run.status, mode="queued")

    done = ingestion_runs.sync_source(
        db, src, _embedder, _vector_store, cache=_cache, triggered_by=principal.user_id,
        trigger_type=ingestion_runs.TRIGGER_REINDEX, run=new_run)
    return SyncResponse(run_id=done.id, status=done.status, mode="inline")


@router.get("/admin/system/status", response_model=SystemStatus, tags=["admin"])
def system_status(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Ops overview: worker liveness, queue depth, ingestion health, source
    health — everything an operator needs at a glance (MON-02)."""
    principal.require_role(tenant_id, "admin")

    rows = (
        db.query(IngestionRun.status, func.count(IngestionRun.id))
        .filter(IngestionRun.tenant_id == tenant_id)
        .group_by(IngestionRun.status).all()
    )
    by_status = {s: int(n) for s, n in rows}
    total = sum(by_status.values())
    active = by_status.get("queued", 0) + by_status.get("running", 0)
    failed = by_status.get("failed", 0)
    succeeded = by_status.get("succeeded", 0) + by_status.get("partial", 0)
    finished = succeeded + failed
    success_rate = round(succeeded / finished, 4) if finished else 1.0

    worker = WorkerStatus(alive=False)
    queue = QueueStatus()
    if _job_queue is not None:
        try:
            last = _job_queue.last_heartbeat()
            if last is not None:
                ago = max(0.0, time.time() - last)
                worker = WorkerStatus(alive=ago < 30, last_heartbeat_seconds_ago=round(ago, 1))
            queue = QueueStatus(**_job_queue.depth())
        except Exception:
            pass

    now = time.time()
    sources = []
    for s in ingestion_runs.list_sources(db, tenant_id):
        if s.last_error_at and (not s.last_success_at or s.last_error_at > s.last_success_at):
            health = "failing"
        elif s.last_success_at is None:
            health = "idle"
        elif (now - s.last_success_at.timestamp()) > 86400:
            health = "stale"
        else:
            health = "healthy"
        sources.append(SourceHealth(
            id=s.id, display_name=s.display_name, source_type=s.source_type,
            enabled=s.enabled, health=health,
            last_success_at=s.last_success_at, last_error_at=s.last_error_at))

    return SystemStatus(
        worker=worker, queue=queue, ingestion_total=total, ingestion_by_status=by_status,
        active_runs=active, failed_runs=failed, success_rate=success_rate, sources=sources)


@router.get("/documents", response_model=list[DocumentSummary], tags=["documents"])
def list_documents(
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """List documents (with chunk counts); filter status=quarantined for ING-07."""
    principal.authorize(tenant_id, collection_id)
    docs = ingestion.list_documents(
        db, tenant_id=tenant_id, collection_id=collection_id,
        status=status, limit=limit,
    )
    if not docs:
        return []
    counts = dict(
        db.query(Chunk.document_id, func.count(Chunk.id))
        .filter(Chunk.document_id.in_([d.id for d in docs]))
        .group_by(Chunk.document_id)
        .all()
    )
    return [
        DocumentSummary(
            id=d.id, collection_id=d.collection_id, source_type=d.source_type,
            source_uri=d.source_uri, status=d.status, doc_metadata=d.doc_metadata,
            created_at=d.created_at, chunk_count=counts.get(d.id, 0),
            source_id=d.source_id,
        )
        for d in docs
    ]


@router.delete(
    "/documents/{document_id}",
    response_model=DocumentDeleteResponse,
    tags=["documents"],
)
def delete_document(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Hard-delete a document + its chunks + its vectors (ING-08).

    tenant_id is required as a query param and scopes the delete so one tenant
    cannot remove another's documents.
    """
    principal.authorize(tenant_id)
    principal.require_role(tenant_id, "admin")
    deleted = ingestion.delete_document(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        vector_store=_vector_store,
        cache=_cache,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    _audit(db, tenant_id, principal, "document.delete",
           {"document_id": str(document_id)})
    return DocumentDeleteResponse(document_id=document_id, deleted=True)


@router.post("/search", response_model=SearchResponse, tags=["search"])
def search(
    payload: SearchRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.authorize(payload.tenant_id, payload.collection_id)
    record_query(payload.tenant_id, "search")
    hits = retrieval.search_chunks(
        db=db,
        tenant_id=payload.tenant_id,
        collection_id=payload.collection_id,
        query=payload.query,
        top_k=payload.top_k,
        embedder=_embedder,
        vector_store=_vector_store,
        cache=_cache,
        no_cache=payload.no_cache,
        metadata_filter=payload.metadata_filter,
    )

    items = [SearchResultItem(**hit) for hit in hits]
    return SearchResponse(results=items, total=len(items))


@router.post("/search/debug", tags=["search"])
def search_debug_endpoint(
    payload: SearchRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """DEBUG ONLY: compare dense candidate pool vs reranked order (RET-03 validation)."""
    principal.authorize(payload.tenant_id, payload.collection_id)
    return search_debug(
        db=db,
        tenant_id=payload.tenant_id,
        collection_id=payload.collection_id,
        query=payload.query,
        embedder=_embedder,
        vector_store=_vector_store,
    )


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.authorize(payload.tenant_id, payload.collection_id)
    record_query(payload.tenant_id, "chat")
    start = time.perf_counter()
    with span("chat.request"):
        grounded, answer, citations = generate_answer(
            db=db,
            query=payload.query,
            tenant_id=payload.tenant_id,
            collection_id=payload.collection_id,
            embedder=_embedder,
            vector_store=_vector_store,
            llm=_llm,
            top_k=payload.top_k,
            cache=_cache,
            no_cache=payload.no_cache,
            metadata_filter=payload.metadata_filter,
        )

    _latency_ms = (time.perf_counter() - start) * 1000
    log_query(
        tenant_id=payload.tenant_id,
        collection_id=payload.collection_id,
        query=payload.query,
        retrieved_chunk_ids=[c["chunk_id"] for c in citations],
        grounded=grounded,
        answer=answer,
        latency_ms=_latency_ms,
    )
    # MON-09: export the generation to the external LLM-observability tool
    # (no-op unless configured). Best-effort; never affects the response.
    llm_observability.get_adapter().record_generation(
        request_id=current_request_id(), tenant_id=payload.tenant_id,
        collection_id=payload.collection_id, query=payload.query, answer=answer,
        grounded=grounded, citations=[str(c["chunk_id"]) for c in citations],
        latency_ms=round(_latency_ms, 2))

    citation_items = [Citation(**c) for c in citations]

    # Persist the turn if attached to a session (UI-03).
    if payload.session_id is not None:
        session = chat_sessions.get_session(db, payload.tenant_id, payload.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        chat_sessions.append_message(db, session.id, "user", payload.query)
        chat_sessions.append_message(
            db, session.id, "assistant", answer,
            citations=[c.model_dump(mode="json") for c in citation_items],
        )

    return ChatResponse(
        grounded=grounded,
        answer=answer,
        citations=citation_items,
    )


@router.post("/sessions", response_model=ChatSessionResponse, status_code=201, tags=["chat"])
def create_session(
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Create a chat session (UI-03)."""
    principal.authorize(payload.tenant_id, payload.collection_id)
    return chat_sessions.create_session(
        db, tenant_id=payload.tenant_id, user_id=payload.user_id,
        collection_id=payload.collection_id, title=payload.title,
    )


@router.get("/sessions", response_model=list[ChatSessionResponse], tags=["chat"])
def list_sessions(
    tenant_id: uuid.UUID,
    user_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """List a user's chat sessions, newest first (UI-03)."""
    principal.authorize(tenant_id)
    return chat_sessions.list_sessions(db, tenant_id, user_id)


@router.get(
    "/sessions/{session_id}/messages",
    response_model=list[ChatMessageResponse],
    tags=["chat"],
)
def session_messages(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Return a session's full message history, restoring it across reloads (UI-03)."""
    principal.authorize(tenant_id)
    session = chat_sessions.get_session(db, tenant_id, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return chat_sessions.get_messages(db, session_id)


@router.post("/chat/stream", tags=["chat"])
def chat_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Streaming grounded chat over SSE (UI-01/06).

    Emits `citations`, then `token` events, then a terminal `done` event, each
    as an SSE `data:` line of JSON.
    """
    principal.authorize(payload.tenant_id, payload.collection_id)

    # Validate the session up front so persistence at the end is guaranteed to
    # target a real, tenant-scoped session (parity with the blocking /chat).
    session = None
    if payload.session_id is not None:
        session = chat_sessions.get_session(db, payload.tenant_id, payload.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

    def event_source():
        events = stream_answer(
            db=db,
            query=payload.query,
            tenant_id=payload.tenant_id,
            collection_id=payload.collection_id,
            embedder=_embedder,
            vector_store=_vector_store,
            llm=_llm,
            top_k=payload.top_k,
            cache=_cache,
            no_cache=payload.no_cache,
            metadata_filter=payload.metadata_filter,
        )
        citations: list = []
        text_parts: list[str] = []
        grounded = False
        for event in events:
            if event.get("type") == "citations":
                citations = event.get("citations", [])
            elif event.get("type") == "token":
                text_parts.append(event.get("text", ""))
            elif event.get("type") == "done":
                grounded = bool(event.get("grounded"))
            yield f"data: {json.dumps(event)}\n\n"

        # Persist the turn so the conversation reloads later (UI-03). Streaming
        # previously dropped history; this mirrors the blocking /chat path.
        if session is not None:
            answer = "".join(text_parts).strip()
            chat_sessions.append_message(db, session.id, "user", payload.query)
            chat_sessions.append_message(
                db, session.id, "assistant",
                answer or settings.NO_ANSWER_MESSAGE,
                citations=citations if grounded else [],
            )

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.post(
    "/feedback", response_model=FeedbackResponse, status_code=201, tags=["feedback"]
)
def submit_feedback(
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Persist human-in-the-loop feedback on an answer (MON-07 / UI-05)."""
    principal.authorize(payload.tenant_id, payload.collection_id)
    row = Feedback(
        tenant_id=payload.tenant_id,
        collection_id=payload.collection_id,
        query=payload.query,
        answer=payload.answer,
        rating=payload.rating,
        comment=payload.comment,
        chunk_ids=[str(c) for c in payload.chunk_ids],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/monitoring/drift", tags=["monitoring"])
def drift_check(
    payload: DriftCheckRequest,
    principal: Principal = Depends(get_principal),
):
    """Check query-distribution drift and raise a drift alert metric (MON-05)."""
    principal.require_admin()
    threshold = payload.threshold if payload.threshold is not None else settings.DRIFT_THRESHOLD
    return drift.check_query_drift(
        _embedder, payload.reference_queries, payload.current_queries, threshold,
    )


@router.get("/cost/report", tags=["cost"])
def cost_report_endpoint(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Per-tenant/collection cost attribution report (MON-08)."""
    principal.authorize(tenant_id)
    return cost_report(db, tenant_id=tenant_id)


@router.post(
    "/eval/scorecard", response_model=ScorecardResponse, tags=["evaluation"]
)
def eval_scorecard(
    payload: ScorecardRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Produce a RAG quality scorecard over a labeled eval set (MON-03)."""
    principal.authorize(payload.tenant_id, payload.collection_id)
    return run_scorecard(
        db=db,
        eval_set=[e.model_dump() for e in payload.eval_set],
        tenant_id=payload.tenant_id,
        collection_id=payload.collection_id,
        embedder=_embedder,
        vector_store=_vector_store,
        llm=_llm,
        k=payload.k,
    )


@router.get(
    "/feedback", response_model=list[FeedbackResponse], tags=["feedback"]
)
def list_feedback(
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """List feedback for the evaluation/feedback dashboard (MON-07)."""
    principal.authorize(tenant_id, collection_id)
    q = db.query(Feedback).filter(Feedback.tenant_id == tenant_id)
    if collection_id is not None:
        q = q.filter(Feedback.collection_id == collection_id)
    return q.order_by(Feedback.created_at.desc()).limit(min(limit, 500)).all()