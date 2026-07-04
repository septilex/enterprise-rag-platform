"""Chat session + message persistence (UI-03)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import ChatMessage, ChatSession


def create_session(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: str,
    collection_id: uuid.UUID | None = None,
    title: str = "",
) -> ChatSession:
    session = ChatSession(
        tenant_id=tenant_id, user_id=user_id,
        collection_id=collection_id, title=title,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def list_sessions(
    db: Session, tenant_id: uuid.UUID, user_id: str, limit: int = 100
) -> list[ChatSession]:
    return (
        db.query(ChatSession)
        .filter(ChatSession.tenant_id == tenant_id, ChatSession.user_id == user_id)
        .order_by(ChatSession.created_at.desc())
        .limit(min(limit, 500))
        .all()
    )


def get_session(
    db: Session, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> ChatSession | None:
    s = db.get(ChatSession, session_id)
    if s is None or s.tenant_id != tenant_id:
        return None
    return s


def get_messages(db: Session, session_id: uuid.UUID) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


def append_message(
    db: Session,
    session_id: uuid.UUID,
    role: str,
    content: str,
    citations: list | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        session_id=session_id, role=role, content=content,
        citations=citations or [],
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg
