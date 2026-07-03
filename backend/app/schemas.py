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


class CollectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    created_at: datetime


class DocumentTextIngestRequest(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=1)


class DocumentIngestResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    chunks_created: int


class SearchRequest(BaseModel):
    tenant_id: uuid.UUID
    collection_id: uuid.UUID
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


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


class ChatResponse(BaseModel):
    grounded: bool
    answer: str
    citations: list[Citation]