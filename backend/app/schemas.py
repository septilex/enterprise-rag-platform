import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    created_at: datetime


class CollectionCreate(BaseModel):
    tenant_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    # ING-05: per-collection chunking. "fixed" | "structure" | "semantic" | "markdown".
    chunking_strategy: str = Field(default="fixed", max_length=50)
    chunking_config: dict = Field(default_factory=dict)


class CollectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    chunking_strategy: str
    chunking_config: dict
    created_at: datetime


class DocumentTextIngestRequest(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=1)
    # Optional stable external id for the source. When omitted, identity is
    # derived from the title so re-uploads are idempotent (ING-04).
    source_uri: str | None = Field(default=None, max_length=1024)
    # Ingest-time tags (source/author/date/classification/ACL) for filtered
    # retrieval (ING-06). Values should be scalars for exact-match filtering.
    metadata: dict = Field(default_factory=dict)


class DocumentIngestResponse(BaseModel):
    # None when the upload was queued for background ingestion — the document
    # row is created by the worker; poll `run_id` for progress instead.
    document_id: uuid.UUID | None = None
    status: str
    chunks_created: int = 0
    # True when the source was already indexed with identical content and
    # nothing was re-processed (ING-04 idempotent no-op).
    reused: bool = False
    # Background ingestion (large uploads, ING-09): set when the file was
    # spooled and enqueued to the worker instead of processed inline.
    run_id: uuid.UUID | None = None
    background: bool = False


class DocumentDeleteResponse(BaseModel):
    document_id: uuid.UUID
    deleted: bool


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    collection_id: uuid.UUID
    source_type: str
    source_uri: str
    status: str
    doc_metadata: dict
    created_at: datetime
    chunk_count: int = 0
    source_id: uuid.UUID | None = None


class DriftCheckRequest(BaseModel):
    reference_queries: list[str] = Field(..., min_length=1)
    current_queries: list[str] = Field(..., min_length=1)
    threshold: float | None = Field(default=None, ge=0.0, le=2.0)


class TenantRole(BaseModel):
    tenant_id: uuid.UUID
    role: str


class MeResponse(BaseModel):
    authenticated: bool
    superuser: bool
    user_id: uuid.UUID | None = None
    email: str | None = None
    tenants: list[TenantRole] = Field(default_factory=list)


class BootstrapRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    tenant_name: str = Field(..., min_length=1, max_length=255)


class MembershipCreate(BaseModel):
    tenant_id: uuid.UUID
    email: str = Field(..., min_length=3, max_length=320)
    role: str = Field(..., pattern="^(admin|editor|viewer)$")


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor: str
    action: str
    target: dict
    created_at: datetime


class SourceUpdate(BaseModel):
    enabled: bool


class SourceCreate(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    source_type: str = Field(..., min_length=1, max_length=32)  # connector key
    display_name: str = Field(..., min_length=1, max_length=255)
    config: dict = Field(default_factory=dict)


class SyncResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    mode: str  # "queued" | "inline" | "already_active"


class WorkerStatus(BaseModel):
    alive: bool
    last_heartbeat_seconds_ago: float | None = None


class QueueStatus(BaseModel):
    incremental: int = 0
    bulk: int = 0
    dead: int = 0


class SourceHealth(BaseModel):
    id: uuid.UUID
    display_name: str
    source_type: str
    enabled: bool
    health: str  # healthy | stale | failing | idle
    last_success_at: datetime | None
    last_error_at: datetime | None


class SystemStatus(BaseModel):
    api: str = "ok"
    worker: WorkerStatus
    queue: QueueStatus
    ingestion_total: int
    ingestion_by_status: dict
    active_runs: int
    failed_runs: int
    success_rate: float
    sources: list[SourceHealth]


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    collection_id: uuid.UUID | None
    source_type: str
    display_name: str
    external_ref: str | None
    enabled: bool
    last_success_at: datetime | None
    last_error_at: datetime | None
    created_at: datetime


class IngestionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID
    collection_id: uuid.UUID | None
    trigger_type: str
    status: str
    documents_seen: int
    documents_indexed: int
    documents_quarantined: int
    documents_deleted: int = 0
    chunks_created: int
    chunks_reused: int
    error_summary: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class WebhookIngestRequest(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    source_type: str = Field(..., min_length=1)  # registered connector key
    config: dict = Field(default_factory=dict)    # connector constructor kwargs


class WebhookIngestResponse(BaseModel):
    source_type: str
    seen: int
    ingested: int
    reused: int


class SearchRequest(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    no_cache: bool = False  # CACHE-08 per-request bypass
    metadata_filter: dict = Field(default_factory=dict)  # ING-06 / RET-04


class SearchResultItem(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    score: float
    doc_metadata: dict


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    total: int


class Citation(BaseModel):
    index: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    snippet: str


class ChatRequest(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    query: str = Field(..., min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=20)
    no_cache: bool = False  # CACHE-08 per-request bypass
    metadata_filter: dict = Field(default_factory=dict)  # ING-06 / RET-04
    session_id: uuid.UUID | None = None  # UI-03: persist turn to this session


class ChatSessionCreate(BaseModel):
    tenant_id: uuid.UUID
    user_id: str = Field(..., min_length=1, max_length=255)
    collection_id: uuid.UUID | None = None
    title: str = Field(default="", max_length=512)


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    collection_id: uuid.UUID | None
    user_id: str
    title: str
    created_at: datetime


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    citations: list
    created_at: datetime


class ChatResponse(BaseModel):
    grounded: bool
    answer: str
    citations: list[Citation]


class FeedbackCreate(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    query: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    rating: str = Field(..., pattern="^(up|down)$")
    comment: str | None = Field(default=None, max_length=2000)
    chunk_ids: list[uuid.UUID] = Field(default_factory=list)


class EvalExample(BaseModel):
    query: str = Field(..., min_length=1)
    relevant_chunk_ids: list[uuid.UUID] = Field(default_factory=list)


class ScorecardRequest(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    k: int = Field(default=5, ge=1, le=20)
    eval_set: list[EvalExample] = Field(..., min_length=1)


class ScorecardResponse(BaseModel):
    n: int
    k: int
    precision_at_k: float
    recall_at_k: float
    groundedness: float
    hallucination_rate: float


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    query: str
    answer: str
    rating: str
    comment: str | None
    chunk_ids: list
    created_at: datetime