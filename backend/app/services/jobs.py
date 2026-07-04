"""Ingestion job queue for horizontal backfill throughput (ING-09).

Two Redis-list queues give bulk backfills their own lane so a 100K-document
backfill never blocks time-sensitive incremental updates: workers always drain
the ``incremental`` queue before the ``bulk`` queue. Any number of worker
processes can consume concurrently.
"""

from __future__ import annotations

import json

from prometheus_client import Gauge

INCREMENTAL = "jobs:incremental"
BULK = "jobs:bulk"

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

    def depth(self) -> dict:
        return {
            "incremental": self._r.llen(INCREMENTAL),
            "bulk": self._r.llen(BULK),
        }

    def _refresh_depth(self) -> None:
        d = self.depth()
        QUEUE_DEPTH.labels("incremental").set(d["incremental"])
        QUEUE_DEPTH.labels("bulk").set(d["bulk"])


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
