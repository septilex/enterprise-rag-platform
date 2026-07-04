import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router, _vector_store
from app.core.config import settings
from app.observability import MetricsMiddleware, metrics_response
from app.tracing import setup_tracing

logging.basicConfig(level=logging.INFO)
setup_tracing()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure Qdrant collection exists."""
    _vector_store.ensure_collection()
    yield


app = FastAPI(title="Enterprise RAG Platform", lifespan=lifespan)

# CORS must be added before routes. Starlette's CORSMiddleware answers preflight
# OPTIONS itself (before route dependencies run), so the API-key dependency never
# blocks preflight. X-API-Key is allowed as a request header.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MetricsMiddleware)


@app.get("/health")
def health():
    """Liveness: process is up (INFRA-05)."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: dependencies reachable; 503 during cold start / index load (INFRA-05)."""
    from fastapi import Response
    from sqlalchemy import text

    from app.db.base import SessionLocal

    checks = {"database": False, "vector_store": False}
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        checks["database"] = True
    except Exception:
        pass
    try:
        _vector_store.ensure_collection()
        checks["vector_store"] = True
    except Exception:
        pass

    ok = all(checks.values())
    return Response(
        content=("ready" if ok else "not-ready"),
        status_code=200 if ok else 503,
        headers={"x-checks": ",".join(k for k, v in checks.items() if v)},
    )


@app.get("/metrics")
def metrics():
    """Prometheus scrape endpoint (MON-02)."""
    return metrics_response()


app.include_router(api_router)