import json
import time
import uuid

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
from app.db.models import Feedback
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
from app.observability import log_query, audit_log
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
    return "superuser" if principal.superuser else str(principal.tenant_id)


def _build_job_queue():
    """Redis-backed ingestion queue when Redis is configured (ING-09)."""
    if not settings.CACHE_ENABLED:
        return None
    import redis

    from app.services.jobs import RedisJobQueue

    return RedisJobQueue(redis.Redis.from_url(settings.REDIS_URL, decode_responses=True))


_job_queue = _build_job_queue()


@router.post("/tenants", response_model=TenantResponse, status_code=201, tags=["tenants"])
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.require_admin()  # tenant creation is an admin action (SEC-02)
    try:
        tenant = ingestion.create_tenant(db, name=payload.name)
        audit_log("tenant.create", _actor(principal),
                  {"tenant_id": str(tenant.id), "name": tenant.name})
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
    try:
        collection = ingestion.create_collection(
            db,
            tenant_id=payload.tenant_id,
            name=payload.name,
            chunking_strategy=payload.chunking_strategy,
            chunking_config=payload.chunking_config,
        )
        audit_log("collection.create", _actor(principal),
                  {"tenant_id": str(payload.tenant_id),
                   "collection_id": str(collection.id), "name": collection.name})
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
    try:
        connector = connectors.get_connector(payload.source_type, **payload.config)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    summary = connectors.run_connector(
        db, connector, payload.tenant_id, payload.collection_id,
        _embedder, _vector_store, cache=_cache,
    )
    audit_log("ingest.webhook", _actor(principal),
              {"tenant_id": str(payload.tenant_id),
               "collection_id": str(payload.collection_id),
               "source_type": payload.source_type, **summary})
    return WebhookIngestResponse(**summary)


@router.post("/documents/text", response_model=DocumentIngestResponse, status_code=201, tags=["documents"])
def ingest_text_document(
    payload: DocumentTextIngestRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.authorize(payload.tenant_id, payload.collection_id)
    try:
        document, count, reused = ingestion.ingest_text_document(
            db,
            tenant_id=payload.tenant_id,
            collection_id=payload.collection_id,
            title=payload.title,
            content=payload.content,
            embedder=_embedder,
            vector_store=_vector_store,
            source_uri=payload.source_uri,
            cache=_cache,
            metadata=payload.metadata,
        )
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
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """UI-07: upload a file as retrieval context for a tenant/collection.

    The file is decoded as text and run through the idempotent ingest pipeline.
    It is tagged with upload/session metadata so it can be scoped or filtered.
    Non-text/corrupt files are quarantined (ING-07), not silently dropped.
    """
    principal.authorize(tenant_id, collection_id)
    raw = file.file.read()
    try:
        text = ingestion.extract_text_from_upload(
            file.filename or "", file.content_type or "", raw
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    metadata = {"uploaded": True, "filename": file.filename or "upload"}
    if session_id is not None:
        metadata["session_id"] = str(session_id)
    try:
        document, count, reused = ingestion.ingest_text_document(
            db,
            tenant_id=tenant_id,
            collection_id=collection_id,
            title=file.filename or f"upload-{uuid.uuid4()}",
            content=text,
            embedder=_embedder,
            vector_store=_vector_store,
            source_uri=f"upload://{file.filename}",
            cache=_cache,
            metadata=metadata,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    audit_log("document.upload", _actor(principal),
              {"tenant_id": str(tenant_id), "collection_id": str(collection_id),
               "document_id": str(document.id), "filename": file.filename})
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
    result = ingestion.erase_document(
        db, tenant_id=tenant_id, document_id=document_id,
        vector_store=_vector_store, cache=_cache,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    audit_log("document.erase", _actor(principal),
              {"tenant_id": str(tenant_id), **result})
    return result


@router.get("/documents", response_model=list[DocumentSummary], tags=["documents"])
def list_documents(
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """List documents; filter by status=quarantined for the failure view (ING-07)."""
    principal.authorize(tenant_id, collection_id)
    return ingestion.list_documents(
        db, tenant_id=tenant_id, collection_id=collection_id,
        status=status, limit=limit,
    )


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
    deleted = ingestion.delete_document(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        vector_store=_vector_store,
        cache=_cache,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    audit_log("document.delete", _actor(principal),
              {"tenant_id": str(tenant_id), "document_id": str(document_id)})
    return DocumentDeleteResponse(document_id=document_id, deleted=True)


@router.post("/search", response_model=SearchResponse, tags=["search"])
def search(
    payload: SearchRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.authorize(payload.tenant_id, payload.collection_id)
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

    log_query(
        tenant_id=payload.tenant_id,
        collection_id=payload.collection_id,
        query=payload.query,
        retrieved_chunk_ids=[c["chunk_id"] for c in citations],
        grounded=grounded,
        answer=answer,
        latency_ms=(time.perf_counter() - start) * 1000,
    )

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
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"

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