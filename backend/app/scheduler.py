"""Scheduled ingestion (ING-03 cron/polling).

Periodically enqueues sync jobs for enabled connector sources whose
``config.schedule_seconds`` interval has elapsed. Idempotent: a source with an
in-flight run is skipped, so scheduled + manual triggers never duplicate runs.

Run:  python -m app.scheduler
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import Source
from app.services import ingestion_runs

log = logging.getLogger("rag.scheduler")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def due_sources(db: Session, now: datetime | None = None) -> list[Source]:
    """Enabled connector sources whose schedule interval has elapsed."""
    now = now or _now()
    out: list[Source] = []
    sources = (
        db.query(Source)
        .filter(Source.enabled.is_(True))
        .all()
    )
    for s in sources:
        cfg = s.config or {}
        interval = cfg.get("schedule_seconds")
        if not interval or not cfg.get("connector_type"):
            continue
        last = s.last_success_at
        if last is None or (now - last).total_seconds() >= interval:
            out.append(s)
    return out


def tick(db: Session, queue) -> int:
    """Enqueue a scheduled sync for every due source. Returns count enqueued."""
    enqueued = 0
    for src in due_sources(db):
        if ingestion_runs.active_run_for_source(db, src.id) is not None:
            continue  # idempotent: already in flight
        run = ingestion_runs.create_queued_run(
            db, src, trigger_type=ingestion_runs.TRIGGER_SCHEDULED)
        queue.enqueue({
            "kind": "sync_source", "source_id": str(src.id), "run_id": str(run.id),
            "trigger_type": ingestion_runs.TRIGGER_SCHEDULED, "attempt": 0,
        })
        enqueued += 1
    return enqueued


def main() -> None:  # pragma: no cover - long-running loop
    from app.observability import setup_logging
    setup_logging()
    import redis
    from app.services.jobs import RedisJobQueue

    queue = RedisJobQueue(redis.Redis.from_url(settings.REDIS_URL, decode_responses=True))
    log.info("ingestion scheduler started (interval %ss)", settings.SCHEDULER_INTERVAL)
    while True:
        db = SessionLocal()
        try:
            n = tick(db, queue)
            if n:
                log.info("scheduled %s sync job(s)", n)
        except Exception as exc:  # never crash the scheduler loop
            log.warning("scheduler tick failed: %s", exc)
        finally:
            db.close()
        time.sleep(settings.SCHEDULER_INTERVAL)


if __name__ == "__main__":  # pragma: no cover
    main()
