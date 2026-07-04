"""Embedding / query-distribution drift detection (MON-05).

Compares the centroid of a current batch of query embeddings against a
reference batch; a large cosine shift signals distribution drift and raises a
drift alert metric so Alertmanager can fire (MON-06).
"""

from __future__ import annotations

import math

from app.observability import DRIFT_ALERTS, DRIFT_SCORE
from app.services.embedder import Embedder


def _centroid(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / n for i in range(dim)]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return 1.0 - dot / (na * nb)


def detect_drift(
    reference: list[list[float]],
    current: list[list[float]],
    threshold: float,
) -> tuple[bool, float]:
    """Return (drifted, score) where score is centroid cosine distance."""
    if not reference or not current:
        return False, 0.0
    score = _cosine_distance(_centroid(reference), _centroid(current))
    return score > threshold, score


def check_query_drift(
    embedder: Embedder,
    reference_queries: list[str],
    current_queries: list[str],
    threshold: float,
) -> dict:
    """Embed both query sets, measure drift, and emit metrics/alerts (MON-05)."""
    ref = embedder.embed(reference_queries)
    cur = embedder.embed(current_queries)
    drifted, score = detect_drift(ref, cur, threshold)
    DRIFT_SCORE.set(score)
    if drifted:
        DRIFT_ALERTS.inc()
    return {"drifted": drifted, "score": round(score, 6), "threshold": threshold}
