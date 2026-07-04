"""Usage recording + cost attribution reporting (MON-08)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import UsageEvent

LLM_TOKENS = "llm_tokens"
EMBED_TEXTS = "embed_texts"


def record_usage(
    db: Session,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID | None,
    kind: str,
    quantity: int,
) -> None:
    """Persist a usage event. Best-effort: never breaks the request path."""
    if db is None or quantity <= 0:
        return
    db.add(UsageEvent(
        tenant_id=tenant_id, collection_id=collection_id,
        kind=kind, quantity=quantity,
    ))
    db.commit()


def _estimated_cost(kind: str, quantity: int) -> float:
    if kind == LLM_TOKENS:
        return quantity / 1000 * settings.COST_PER_1K_LLM_TOKENS
    if kind == EMBED_TEXTS:
        return quantity / 1000 * settings.COST_PER_1K_EMBED_TEXTS
    return 0.0


def cost_report(
    db: Session,
    tenant_id: uuid.UUID,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    """Break down usage + estimated cost by collection and kind for a period."""
    q = db.query(
        UsageEvent.collection_id,
        UsageEvent.kind,
        func.coalesce(func.sum(UsageEvent.quantity), 0),
    ).filter(UsageEvent.tenant_id == tenant_id)
    if start is not None:
        q = q.filter(UsageEvent.created_at >= start)
    if end is not None:
        q = q.filter(UsageEvent.created_at <= end)
    q = q.group_by(UsageEvent.collection_id, UsageEvent.kind)

    lines: list[dict] = []
    total_cost = 0.0
    for collection_id, kind, quantity in q.all():
        cost = round(_estimated_cost(kind, int(quantity)), 6)
        total_cost += cost
        lines.append({
            "collection_id": str(collection_id) if collection_id else None,
            "kind": kind,
            "quantity": int(quantity),
            "estimated_cost": cost,
        })
    return {
        "tenant_id": str(tenant_id),
        "lines": lines,
        "total_estimated_cost": round(total_cost, 6),
    }
