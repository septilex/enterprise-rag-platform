"""Pluggable ingestion connector framework (ING-01).

New source types (file share, S3, Confluence, DB, web crawl, ...) are added by
implementing :class:`Connector` and registering it — no change to core
ingestion code. A registered connector yields SourceDocuments which are fed
through the existing idempotent ingest path (ING-04).
"""

from __future__ import annotations

import abc
import hashlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

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
    # cursor/version for delta detection (e.g. mtime, etag). Optional.
    version: str | None = None
    metadata: dict = field(default_factory=dict)

    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass
class FetchResult:
    """A delta fetch: changed documents, source_uris deleted since the last
    cursor, and the new connector cursor state (ING-02/ING-08)."""

    documents: list[SourceDocument]
    cursor: dict
    deleted: list[str] = field(default_factory=list)


class Connector(abc.ABC):
    """Interface every source connector implements.

    ``kind`` distinguishes push connectors (data arrives via API, e.g. manual
    upload) from pull connectors (the platform fetches, e.g. filesystem/s3).
    """

    #: unique key used to register/select the connector
    source_type: str
    kind: str = "pull"

    @abc.abstractmethod
    def fetch(self) -> Iterator[SourceDocument]:
        """Yield all source documents (full scan)."""
        ...

    def fetch_delta(self, cursor: dict | None) -> FetchResult:
        """Yield only documents new/changed since ``cursor`` (ING-02).

        Default: full scan with hash-based cursor so unchanged docs are skipped
        by the idempotent pipeline. Connectors override for native delta.
        """
        cursor = cursor or {}
        seen_hashes = cursor.get("hashes", {})
        changed: list[SourceDocument] = []
        new_hashes: dict[str, str] = {}
        for doc in self.fetch():
            h = doc.content_hash()
            new_hashes[doc.source_uri] = h
            if seen_hashes.get(doc.source_uri) != h:
                changed.append(doc)
        deleted = [uri for uri in seen_hashes if uri not in new_hashes]
        return FetchResult(documents=changed, cursor={"hashes": new_hashes}, deleted=deleted)


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
    """Ingests .txt/.md files from a directory tree (file-share style source).

    Native delta via file mtime: only files modified since the last cursor are
    re-ingested; deletions are detected and reported for propagation.
    """

    source_type = "filesystem"

    def __init__(self, root: str, extensions: tuple[str, ...] = (".txt", ".md")):
        self.root = root
        self.extensions = tuple(extensions)

    def _iter_files(self):
        for dirpath, _dirs, files in os.walk(self.root):
            for name in sorted(files):
                if name.lower().endswith(self.extensions):
                    yield os.path.join(dirpath, name), name

    def fetch(self) -> Iterator[SourceDocument]:
        for path, name in self._iter_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            if content.strip():
                yield SourceDocument(
                    title=name, content=content, source_uri=f"file://{path}",
                    version=str(os.path.getmtime(path)),
                )

    def fetch_delta(self, cursor: dict | None) -> FetchResult:
        cursor = cursor or {}
        last = cursor.get("mtimes", {})
        changed: list[SourceDocument] = []
        new_mtimes: dict[str, str] = {}
        for path, name in self._iter_files():
            uri = f"file://{path}"
            mtime = str(os.path.getmtime(path))
            new_mtimes[uri] = mtime
            if last.get(uri) != mtime:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                if content.strip():
                    changed.append(SourceDocument(
                        title=name, content=content, source_uri=uri, version=mtime))
        deleted = [uri for uri in last if uri not in new_mtimes]
        return FetchResult(documents=changed, cursor={"mtimes": new_mtimes}, deleted=deleted)


@register_connector
class S3MockConnector(Connector):
    """External-object-store-style connector (S3/blob simulation).

    Reads objects from a local directory treated as a bucket, or from an
    in-memory ``objects`` map for tests. Delta via per-object etag (content
    hash), demonstrating the same shape a real S3 connector would use.
    """

    source_type = "s3_mock"

    def __init__(self, bucket_path: str | None = None, objects: dict | None = None,
                 prefix: str = ""):
        self.bucket_path = bucket_path
        self.objects = objects  # {key: content}
        self.prefix = prefix

    def _iter_objects(self):
        if self.objects is not None:
            for key, content in sorted(self.objects.items()):
                if key.startswith(self.prefix):
                    yield key, content
        elif self.bucket_path:
            for dirpath, _dirs, files in os.walk(self.bucket_path):
                for name in sorted(files):
                    path = os.path.join(dirpath, name)
                    key = os.path.relpath(path, self.bucket_path).replace(os.sep, "/")
                    if key.startswith(self.prefix):
                        with open(path, encoding="utf-8", errors="replace") as fh:
                            yield key, fh.read()

    def fetch(self) -> Iterator[SourceDocument]:
        for key, content in self._iter_objects():
            if content.strip():
                doc = SourceDocument(
                    title=key.rsplit("/", 1)[-1], content=content,
                    source_uri=f"s3://{key}")
                doc.version = doc.content_hash()
                yield doc

    def fetch_delta(self, cursor: dict | None) -> FetchResult:
        cursor = cursor or {}
        last = cursor.get("etags", {})
        changed: list[SourceDocument] = []
        new_etags: dict[str, str] = {}
        for key, content in self._iter_objects():
            if not content.strip():
                continue
            uri = f"s3://{key}"
            etag = hashlib.sha256(content.encode()).hexdigest()
            new_etags[uri] = etag
            if last.get(uri) != etag:
                changed.append(SourceDocument(
                    title=key.rsplit("/", 1)[-1], content=content,
                    source_uri=uri, version=etag))
        deleted = [uri for uri in last if uri not in new_etags]
        return FetchResult(documents=changed, cursor={"etags": new_etags}, deleted=deleted)


@register_connector
class S3Connector(Connector):
    """Production S3/blob connector (real AWS SDK, isolated adapter).

    Requires ``boto3`` (see requirements-connectors.txt) and standard AWS creds
    in the environment / instance role. Etag-based delta via the default
    ``fetch_delta``. Kept separate from ``s3_mock`` so tests never need AWS.
    """

    source_type = "s3"

    def __init__(self, bucket: str, prefix: str = "", region: str | None = None,
                 endpoint_url: str | None = None, extensions: tuple[str, ...] = (".txt", ".md")):
        self.bucket = bucket
        self.prefix = prefix
        self.region = region
        self.endpoint_url = endpoint_url
        self.extensions = tuple(extensions)

    def _client(self):
        try:
            import boto3  # optional heavy dep
        except ImportError as exc:
            raise RuntimeError(
                "S3 connector requires boto3 (pip install -r requirements-connectors.txt)"
            ) from exc
        return boto3.client("s3", region_name=self.region, endpoint_url=self.endpoint_url)

    def fetch(self) -> Iterator[SourceDocument]:
        client = self._client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.lower().endswith(self.extensions):
                    continue
                body = client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
                content = body.decode("utf-8", errors="replace")
                if content.strip():
                    yield SourceDocument(
                        title=key.rsplit("/", 1)[-1], content=content,
                        source_uri=f"s3://{self.bucket}/{key}",
                        version=obj.get("ETag", "").strip('"'))


@register_connector
class ConfluenceConnector(Connector):
    """Production Confluence connector (real REST API, isolated adapter).

    Fetches page bodies from a space via the Confluence Cloud REST API using an
    API token. Uses ``httpx`` (already a dependency). Version-aware delta via the
    page version number.
    """

    source_type = "confluence"

    def __init__(self, base_url: str, token: str, space_key: str,
                 email: str | None = None, limit: int = 100):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.space_key = space_key
        self.email = email
        self.limit = limit

    @staticmethod
    def _strip_html(html: str) -> str:
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()

    def fetch(self) -> Iterator[SourceDocument]:
        import httpx

        auth = (self.email, self.token) if self.email else None
        headers = {} if self.email else {"Authorization": f"Bearer {self.token}"}
        start = 0
        with httpx.Client(timeout=15) as client:
            while True:
                r = client.get(
                    f"{self.base_url}/wiki/rest/api/content",
                    params={"spaceKey": self.space_key, "expand": "body.storage,version",
                            "start": start, "limit": self.limit},
                    auth=auth, headers=headers)
                r.raise_for_status()
                data = r.json()
                for page in data.get("results", []):
                    body = page.get("body", {}).get("storage", {}).get("value", "")
                    content = self._strip_html(body)
                    if content:
                        yield SourceDocument(
                            title=page.get("title", page["id"]), content=content,
                            source_uri=f"confluence://{self.space_key}/{page['id']}",
                            version=str(page.get("version", {}).get("number", "")))
                if data.get("size", 0) < self.limit:
                    break
                start += self.limit


def build_connector(source_type: str, config: dict) -> Connector:
    """Instantiate a connector from a Source's stored config (worker/sync use)."""
    kwargs = {k: v for k, v in (config or {}).items()
              if k not in ("connector_type", "cursor", "schedule_seconds")}
    return get_connector(source_type, **kwargs)


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
