from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as api_router, _vector_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure Qdrant collection exists."""
    _vector_store.ensure_collection()
    yield


app = FastAPI(title="Enterprise RAG Platform", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(api_router)