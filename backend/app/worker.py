"""Background ingestion worker (ING-09 orchestration).

Drains the Redis job queue and executes ingestion jobs off the request path, so
API latency is never coupled to ingestion. Jobs move a run through
queued -> running -> succeeded/partial/failed, with bounded retries on failure.

Run one or more workers:  python -m app.worker
"""

from __future__ import annotations

import logging
import time
import uuid

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import IngestionRun, Source
from app.services import ingestion_runs
from app.services.jobs import RedisJobQueue

log = logging.getLogger("rag.worker")

JOB_SYNC_SOURCE = "sync_source"
MAX_ATTEMPTS = 3


def _embedder():
    from app.services.embedder import CachedEmbedder, OpenAIEmbedder
    from app.services.cache import build_cache

    cache = build_cache()
    raw = OpenAIEmbedder()
    emb = (CachedEmbedder(raw, cache, raw.model, settings.EMBED_CACHE_TTL)
           if cache is not None else raw)
    return emb, cache


def _vector_store():
    from app.services.vector_store import QdrantVectorStore
    return QdrantVectorStore()


def handle_job(db, job: dict, embedder, vector_store, cache, queue: RedisJobQueue) -> None:
    """Execute a single job. On failure, retry up to MAX_ATTEMPTS then fail the run."""
    kind = job.get("kind")
    if kind != JOB_SYNC_SOURCE:
        log.warning("unknown job kind: %s", kind)
        return

    source = db.get(Source, uuid.UUID(job["source_id"]))
    run = db.get(IngestionRun, uuid.UUID(job["run_id"])) if job.get("run_id") else None
    if source is None or run is None:
        log.warning("job references missing source/run: %s", job)
        return

    attempt = int(job.get("attempt", 0))
    try:
        ingestion_runs.sync_source(
            db, source, embedder, vector_store, cache=cache,
            triggered_by=(uuid.UUID(job["triggered_by"]) if job.get("triggered_by") else None),
            trigger_type=job.get("trigger_type", ingestion_runs.TRIGGER_SCHEDULED),
            run=run,
        )
    except Exception as exc:  # noqa: BLE001 - worker must not crash
        db.rollback()
        if attempt + 1 < MAX_ATTEMPTS:
            log.warning("job failed (attempt %s), requeuing: %s", attempt + 1, exc)
            run.status = ingestion_runs.STATUS_QUEUED
            run.error_summary = f"retry {attempt + 1}: {exc}"[:1000]
            db.commit()
            queue.enqueue({**job, "attempt": attempt + 1})
        else:
            log.error("job failed permanently: %s", exc)
            run.status = ingestion_runs.STATUS_FAILED
            run.error_summary = str(exc)[:1000]
            db.commit()
            queue.dead_letter(job)  # DLQ for operator visibility


def run_once(queue: RedisJobQueue, embedder, vector_store, cache) -> bool:
    """Process a single job. Returns False if the queue was empty."""
    job = queue.dequeue()
    if job is None:
        return False
    db = SessionLocal()
    try:
        handle_job(db, job, embedder, vector_store, cache, queue)
    finally:
        db.close()
    return True


def main() -> None:  # pragma: no cover - long-running loop
    from app.observability import setup_logging
    setup_logging()
    import redis
    queue = RedisJobQueue(redis.Redis.from_url(settings.REDIS_URL, decode_responses=True))
    embedder, cache = _embedder()
    vector_store = _vector_store()
    # Recover runs orphaned by a previous crash before consuming new work.
    db = SessionLocal()
    try:
        recovered = ingestion_runs.recover_stuck_runs(db)
        if recovered:
            log.warning("recovered %s stuck run(s) on startup", recovered)
    finally:
        db.close()
    log.info("ingestion worker started")
    idle = 0
    while True:
        queue.heartbeat()  # liveness signal for the ops dashboard
        did = run_once(queue, embedder, vector_store, cache)
        idle = 0 if did else min(idle + 1, 10)
        if not did:
            time.sleep(0.5 + idle * 0.1)


if __name__ == "__main__":  # pragma: no cover
    main()
