"""Vector store interface and Qdrant implementation."""

import abc
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings


class VectorStore(abc.ABC):
    """Abstract vector store — upsert vectors with payload."""

    @abc.abstractmethod
    def ensure_collection(self) -> None:
        """Create the collection if it does not exist."""
        ...

    @abc.abstractmethod
    def upsert(
        self,
        ids: list[uuid.UUID],
        vectors: list[list[float]],
        payloads: list[dict],
    ) -> None:
        ...

    @abc.abstractmethod
    def search(
        self,
        vector: list[float],
        filters: dict,
        top_k: int = 5,
    ) -> list[dict]:
        """Return list of dicts with keys: id, score, payload."""
        ...

    @abc.abstractmethod
    def delete(self, ids: list[uuid.UUID]) -> None:
        """Remove points by id (idempotent — unknown ids are ignored)."""
        ...


class QdrantVectorStore(VectorStore):
    """Qdrant implementation of VectorStore."""

    # A big document is thousands of points; one monolithic upsert is a
    # multi-MB HTTP request that times out / gets aborted. Batching keeps each
    # request small and lets progress survive transient hiccups.
    UPSERT_BATCH = 256

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        collection_name: str | None = None,
        vector_size: int | None = None,
    ):
        self.host = host or settings.QDRANT_HOST
        self.port = port or settings.QDRANT_PORT
        self.collection_name = collection_name or settings.QDRANT_COLLECTION
        self.vector_size = vector_size or settings.QDRANT_VECTOR_SIZE
        # Default client timeout is a few seconds — too tight for indexing a
        # batch of points with wait=true on a busy node.
        self._client = QdrantClient(host=self.host, port=self.port, timeout=60)

    def ensure_collection(self) -> None:
        """Create collection if it doesn't already exist."""
        existing = [c.name for c in self._client.get_collections().collections]
        if self.collection_name not in existing:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=Distance.COSINE,
                ),
            )

    def upsert(
        self,
        ids: list[uuid.UUID],
        vectors: list[list[float]],
        payloads: list[dict],
    ) -> None:
        points = [
            PointStruct(
                id=str(point_id),
                vector=vector,
                payload=payload,
            )
            for point_id, vector, payload in zip(ids, vectors, payloads)
        ]
        for start in range(0, len(points), self.UPSERT_BATCH):
            self._client.upsert(
                collection_name=self.collection_name,
                points=points[start:start + self.UPSERT_BATCH],
            )

    def search(
        self,
        vector: list[float],
        filters: dict,
        top_k: int = 5,
    ) -> list[dict]:
        """Search Qdrant with payload filters. Returns list of {id, score, payload}."""
        conditions = [
            FieldCondition(key=key, match=MatchValue(value=value))
            for key, value in filters.items()
        ]
        response = self._client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=Filter(must=conditions),
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "id": hit.id,
                "score": hit.score,
                "payload": hit.payload,
            }
            for hit in response.points
        ]

    def delete(self, ids: list[uuid.UUID]) -> None:
        """Delete points by id. No-op on empty list; unknown ids are ignored."""
        if not ids:
            return
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=[str(pid) for pid in ids],
        )
