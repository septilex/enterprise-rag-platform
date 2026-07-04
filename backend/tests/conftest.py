"""Shared fixtures. DB-backed tests use the live Postgres from docker-compose;
they self-skip if it is unreachable so the pure-unit suite still runs anywhere.
"""

import uuid

import pytest
from sqlalchemy import text

from app.db.base import SessionLocal, get_db
from app.db.models import Tenant


@pytest.fixture
def db_session():
    try:
        session = SessionLocal()
        session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"Postgres not reachable: {exc}")
        return
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def api_client(db_session, monkeypatch):
    """FastAPI TestClient with fakes injected and the DB dependency overridden,
    so the full HTTP stack is exercised without OpenAI/Qdrant.
    """
    from fastapi.testclient import TestClient

    import app.api.routes as routes
    from app.main import app
    from tests.fakes import FakeEmbedder, FakeLLM, InMemoryVectorStore

    monkeypatch.setattr(routes, "_embedder", FakeEmbedder())
    monkeypatch.setattr(routes, "_vector_store", InMemoryVectorStore())
    monkeypatch.setattr(routes, "_llm", FakeLLM())

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def tenant(db_session):
    """A throwaway tenant; cascade-deletes its collections/documents/chunks."""
    t = Tenant(name=f"test-{uuid.uuid4()}")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    yield t
    db_session.delete(t)
    db_session.commit()
