import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router, _vector_store
from app.core.config import settings
from app.observability import (
    MetricsMiddleware, CorrelationIdMiddleware, metrics_response, setup_logging,
)
from app.tracing import setup_tracing

setup_logging()
setup_tracing()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure Qdrant collection exists + recover any stuck runs."""
    _vector_store.ensure_collection()
    try:
        from app.db.base import SessionLocal
        from app.services.ingestion_runs import recover_stuck_runs

        db = SessionLocal()
        n = recover_stuck_runs(db)
        db.close()
        if n:
            logging.getLogger("rag").warning("recovered %s stuck ingestion run(s)", n)
    except Exception:  # never block startup on recovery
        pass
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
# Added last => runs first: assigns the request id before anything else.
app.add_middleware(CorrelationIdMiddleware)


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