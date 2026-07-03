from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas import (
    TenantCreate,
    TenantResponse,
    CollectionCreate,
    CollectionResponse,
    DocumentTextIngestRequest,
    DocumentIngestResponse,
    SearchRequest,
    SearchResultItem,
    SearchResponse,
    ChatRequest,
    ChatResponse,
    Citation,
)
from app.services import ingestion, retrieval
from app.services.embedder import OpenAIEmbedder
from app.services.generation import generate_answer
from app.services.llm import OpenAILLM
from app.services.vector_store import QdrantVectorStore

router = APIRouter()

_embedder = OpenAIEmbedder()
_vector_store = QdrantVectorStore()
_llm = OpenAILLM()


@router.post("/tenants", response_model=TenantResponse, status_code=201, tags=["tenants"])
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db)):
    try:
        return ingestion.create_tenant(db, name=payload.name)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tenant name already exists")


@router.post("/collections", response_model=CollectionResponse, status_code=201, tags=["collections"])
def create_collection(payload: CollectionCreate, db: Session = Depends(get_db)):
    try:
        return ingestion.create_collection(db, tenant_id=payload.tenant_id, name=payload.name)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Collection name already exists for this tenant")


@router.post("/documents/text", response_model=DocumentIngestResponse, status_code=201, tags=["documents"])
def ingest_text_document(payload: DocumentTextIngestRequest, db: Session = Depends(get_db)):
    try:
        document, count = ingestion.ingest_text_document(
            db,
            tenant_id=payload.tenant_id,
            collection_id=payload.collection_id,
            title=payload.title,
            content=payload.content,
            embedder=_embedder,
            vector_store=_vector_store,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    return DocumentIngestResponse(
        document_id=document.id,
        status=document.status,
        chunks_created=count,
    )


@router.post("/search", response_model=SearchResponse, tags=["search"])
def search(payload: SearchRequest, db: Session = Depends(get_db)):
    hits = retrieval.search_chunks(
        db=db,
        tenant_id=payload.tenant_id,
        collection_id=payload.collection_id,
        query=payload.query,
        top_k=payload.top_k,
        embedder=_embedder,
        vector_store=_vector_store,
    )

    items = [SearchResultItem(**hit) for hit in hits]
    return SearchResponse(results=items, total=len(items))


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    grounded, answer, citations = generate_answer(
        db=db,
        query=payload.query,
        tenant_id=payload.tenant_id,
        collection_id=payload.collection_id,
        embedder=_embedder,
        vector_store=_vector_store,
        llm=_llm,
        top_k=payload.top_k,
    )

    citation_items = [Citation(**c) for c in citations]
    return ChatResponse(
        grounded=grounded,
        answer=answer,
        citations=citation_items,
    )