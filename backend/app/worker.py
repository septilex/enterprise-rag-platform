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
from pathlib import Path

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import IngestionRun, Source
from app.services import ingestion_runs
from app.services.jobs import RedisJobQueue

log = logging.getLogger("rag.worker")

JOB_SYNC_SOURCE = "sync_source"
JOB_INGEST_UPLOAD = "ingest_upload"
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
    if kind not in (JOB_SYNC_SOURCE, JOB_INGEST_UPLOAD):
        log.warning("unknown job kind: %s", kind)
        return

    source = db.get(Source, uuid.UUID(job["source_id"]))
    run = db.get(IngestionRun, uuid.UUID(job["run_id"])) if job.get("run_id") else None
    if source is None or run is None:
        log.warning("job references missing source/run: %s", job)
        return

    attempt = int(job.get("attempt", 0))
    try:
        if kind == JOB_INGEST_UPLOAD:
            _ingest_upload(db, job, source, run, embedder, vector_store, cache)
        else:
            ingestion_runs.sync_source(
                db, source, embedder, vector_store, cache=cache,
                triggered_by=(uuid.UUID(job["triggered_by"]) if job.get("triggered_by") else None),
                trigger_type=job.get("trigger_type", ingestion_runs.TRIGGER_SCHEDULED),
                run=run,
            )
    except _PermanentJobError as exc:
        # Unrecoverable (e.g. unparseable upload) — fail the run immediately,
        # no retries; the failure is visible in Sources & activity (ING-07).
        db.rollback()
        log.error("job failed permanently (no retry): %s", exc)
        run.status = ingestion_runs.STATUS_FAILED
        run.error_summary = str(exc)[:1000]
        run.completed_at = ingestion_runs._now()
        db.commit()
        _cleanup_spool(job)
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
            _cleanup_spool(job)
    else:
        _cleanup_spool(job)


class _PermanentJobError(Exception):
    """Job failure that must not be retried (bad input, not transient infra)."""


def _ingest_upload(db, job: dict, source: Source, run: IngestionRun,
                   embedder, vector_store, cache) -> None:
    """Ingest a spooled background upload (ING-09 bulk lane).

    Parse happens here — off the request path — so huge files never block the
    API. The spool file is removed by the caller once the job leaves the queue
    for good (success or permanent failure), surviving retries in between.
    """
    from app.services import ingestion

    spool_path = Path(job["spool_path"])
    if not spool_path.exists():
        raise _PermanentJobError(f"spooled upload missing: {spool_path.name}")
    raw = spool_path.read_bytes()
    try:
        text = ingestion.extract_text_from_upload(
            job.get("filename") or "", job.get("content_type") or "", raw)
    except ValueError as exc:
        raise _PermanentJobError(f"parse failed: {exc}") from exc

    filename = job.get("filename") or f"upload-{run.id}"
    # ingest_items only applies run_metadata to runs it creates itself, so
    # stamp the pre-created queued run directly for activity-view visibility.
    run.run_metadata = {"filename": filename, "background": True}
    ingestion_runs.ingest_items(
        db, uuid.UUID(job["tenant_id"]), uuid.UUID(job["collection_id"]),
        source,
        items=[{"title": filename, "content": text,
                "source_uri": f"upload://{filename}",
                "metadata": job.get("metadata") or {}}],
        embedder=embedder, vector_store=vector_store,
        trigger_type=ingestion_runs.TRIGGER_MANUAL,
        triggered_by=(uuid.UUID(job["triggered_by"]) if job.get("triggered_by") else None),
        cache=cache,
        run=run,
    )


def _cleanup_spool(job: dict) -> None:
    """Best-effort removal of the spooled upload once the job is finished."""
    path = job.get("spool_path")
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:  # never let cleanup kill the worker loop
        log.warning("could not remove spool file %s: %s", path, exc)


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
