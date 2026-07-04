"""Pluggable ingestion connector framework (ING-01).

New source types (file share, S3, Confluence, DB, web crawl, ...) are added by
implementing :class:`Connector` and registering it — no change to core
ingestion code. A registered connector yields SourceDocuments which are fed
through the existing idempotent ingest path (ING-04).
"""

from __future__ import annotations

import abc
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services import ingestion
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore


@dataclass
class SourceDocument:
    """A single unit of source content to be ingested."""

    title: str
    content: str
    source_uri: str  # stable id -> drives idempotency/delta (ING-04)


class Connector(abc.ABC):
    """Interface every source connector implements."""

    #: unique key used to register/select the connector
    source_type: str

    @abc.abstractmethod
    def fetch(self) -> Iterator[SourceDocument]:
        """Yield source documents from the backing system."""
        ...


_REGISTRY: dict[str, type[Connector]] = {}


def register_connector(cls: type[Connector]) -> type[Connector]:
    """Class decorator that registers a connector by its ``source_type``."""
    if not getattr(cls, "source_type", None):
        raise ValueError("connector must define a non-empty source_type")
    _REGISTRY[cls.source_type] = cls
    return cls


def get_connector(source_type: str, **kwargs) -> Connector:
    if source_type not in _REGISTRY:
        raise ValueError(f"unknown connector source_type: {source_type}")
    return _REGISTRY[source_type](**kwargs)


def available_connectors() -> list[str]:
    return sorted(_REGISTRY)


@register_connector
class TextConnector(Connector):
    """In-memory connector over a provided list of documents (baseline/testing)."""

    source_type = "text_batch"

    def __init__(self, documents: list[dict]):
        # documents: [{"title", "content", "source_uri"?}]
        self._documents = documents

    def fetch(self) -> Iterator[SourceDocument]:
        for d in self._documents:
            uri = d.get("source_uri") or f"text_batch://{d['title']}"
            yield SourceDocument(title=d["title"], content=d["content"], source_uri=uri)


@register_connector
class FilesystemConnector(Connector):
    """Ingests .txt/.md files from a directory tree (file-share style source)."""

    source_type = "filesystem"

    def __init__(self, root: str, extensions: tuple[str, ...] = (".txt", ".md")):
        self.root = root
        self.extensions = extensions

    def fetch(self) -> Iterator[SourceDocument]:
        for dirpath, _dirs, files in os.walk(self.root):
            for name in sorted(files):
                if not name.lower().endswith(self.extensions):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                if content.strip():
                    yield SourceDocument(
                        title=name, content=content, source_uri=f"file://{path}"
                    )


def run_connector(
    db: Session,
    connector: Connector,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    embedder: Embedder,
    vector_store: VectorStore,
    cache=None,
) -> dict:
    """Ingest every document a connector yields through the idempotent pipeline.

    Returns a summary: documents seen, ingested (new/changed), and reused
    (unchanged, skipped) — so re-runs are safe and observable (ING-02/ING-04).
    """
    seen = ingested = reused = 0
    for doc in connector.fetch():
        seen += 1
        _document, _count, was_reused = ingestion.ingest_text_document(
            db,
            tenant_id=tenant_id,
            collection_id=collection_id,
            title=doc.title,
            content=doc.content,
            embedder=embedder,
            vector_store=vector_store,
            source_uri=doc.source_uri,
            cache=cache,
        )
        if was_reused:
            reused += 1
        else:
            ingested += 1
    return {"source_type": connector.source_type, "seen": seen,
            "ingested": ingested, "reused": reused}
