"""Ingestion job queue for horizontal backfill throughput (ING-09).

Two Redis-list queues give bulk backfills their own lane so a 100K-document
backfill never blocks time-sensitive incremental updates: workers always drain
the ``incremental`` queue before the ``bulk`` queue. Any number of worker
processes can consume concurrently.
"""

from __future__ import annotations

import json
import time

from prometheus_client import Gauge

INCREMENTAL = "jobs:incremental"
BULK = "jobs:bulk"
DEAD = "jobs:dead"                # dead-letter queue for exhausted jobs
HEARTBEAT_KEY = "worker:heartbeat"

QUEUE_DEPTH = Gauge(
    "rag_ingestion_queue_depth", "Pending ingestion jobs", labelnames=("queue",)
)


class RedisJobQueue:
    def __init__(self, client):
        self._r = client

    def enqueue(self, payload: dict, bulk: bool = False) -> None:
        self._r.lpush(BULK if bulk else INCREMENTAL, json.dumps(payload))
        self._refresh_depth()

    def dequeue(self) -> dict | None:
        """Pop the next job, prioritizing incremental over bulk (ING-09)."""
        raw = self._r.rpop(INCREMENTAL)
        if raw is None:
            raw = self._r.rpop(BULK)
        self._refresh_depth()
        return json.loads(raw) if raw is not None else None

    def dead_letter(self, payload: dict) -> None:
        """Park a permanently-failed job for operator visibility (DLQ)."""
        self._r.lpush(DEAD, json.dumps({**payload, "dead_at": time.time()}))
        self._refresh_depth()

    def dead_letters(self, limit: int = 50) -> list[dict]:
        return [json.loads(x) for x in self._r.lrange(DEAD, 0, limit - 1)]

    def depth(self) -> dict:
        return {
            "incremental": self._r.llen(INCREMENTAL),
            "bulk": self._r.llen(BULK),
            "dead": self._r.llen(DEAD),
        }

    # --- worker liveness ---
    def heartbeat(self, ttl: int = 30) -> None:
        """Workers call this each loop so ops can see they're alive."""
        self._r.set(HEARTBEAT_KEY, str(time.time()), ex=ttl)

    def last_heartbeat(self) -> float | None:
        raw = self._r.get(HEARTBEAT_KEY)
        return float(raw) if raw is not None else None

    def _refresh_depth(self) -> None:
        d = self.depth()
        QUEUE_DEPTH.labels("incremental").set(d["incremental"])
        QUEUE_DEPTH.labels("bulk").set(d["bulk"])
        QUEUE_DEPTH.labels("dead").set(d["dead"])


def process_next(db, queue: RedisJobQueue, embedder, vector_store, cache=None) -> bool:
    """Consume one job and ingest it. Returns False if the queue is empty."""
    import uuid as _uuid

    from app.services import ingestion

    job = queue.dequeue()
    if job is None:
        return False
    ingestion.ingest_text_document(
        db,
        tenant_id=_uuid.UUID(job["tenant_id"]),
        collection_id=_uuid.UUID(job["collection_id"]),
        title=job["title"],
        content=job["content"],
        embedder=embedder,
        vector_store=vector_store,
        source_uri=job.get("source_uri"),
        cache=cache,
        metadata=job.get("metadata") or {},
    )
    return True
