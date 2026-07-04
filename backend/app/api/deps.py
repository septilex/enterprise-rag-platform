"""Auth + RBAC dependencies (SEC-01, SEC-02)."""

import json
import uuid
from dataclasses import dataclass, field

from fastapi import Header, HTTPException

from app.core.config import settings


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Reject anonymous requests when API keys are configured (SEC-01).

    No-op when neither API_KEYS nor PRINCIPALS_JSON are configured, so local
    dev/tests are not blocked; production sets one of them.
    """
    keys = settings.api_key_set
    principals = _load_principals()
    if not keys and not principals:
        return
    valid = keys | set(principals)
    if x_api_key is None or x_api_key not in valid:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@dataclass
class Principal:
    """The authenticated caller and its authorization scope (SEC-02)."""

    superuser: bool = False
    tenant_id: uuid.UUID | None = None
    collections: str | set = field(default_factory=set)  # "*" or set of uuids

    def authorize(
        self, tenant_id: uuid.UUID, collection_id: uuid.UUID | None = None
    ) -> None:
        if self.superuser:
            return
        if self.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="Tenant access denied")
        if collection_id is not None and self.collections != "*":
            if collection_id not in self.collections:
                raise HTTPException(
                    status_code=403, detail="Collection access denied"
                )

    def require_admin(self) -> None:
        if not self.superuser:
            raise HTTPException(status_code=403, detail="Admin privilege required")


def _load_principals() -> dict:
    if not settings.PRINCIPALS_JSON.strip():
        return {}
    return json.loads(settings.PRINCIPALS_JSON)


def get_principal(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """Resolve the caller into a Principal.

    - No principals configured -> superuser (open / dev), preserving behavior
      when RBAC is not in use.
    - Principals configured -> the key must map to one; its scope is enforced.
    """
    principals = _load_principals()
    if not principals:
        return Principal(superuser=True)

    entry = principals.get(x_api_key or "")
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    collections = entry.get("collections", "*")
    if collections != "*":
        collections = {uuid.UUID(c) for c in collections}
    return Principal(
        superuser=bool(entry.get("superuser", False)),
        tenant_id=uuid.UUID(entry["tenant_id"]) if entry.get("tenant_id") else None,
        collections=collections,
    )
