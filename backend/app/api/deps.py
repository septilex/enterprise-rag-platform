"""Auth + RBAC dependencies (SEC-01, SEC-02).

Auth modes (settings.AUTH_MODE):
- "open"  (default): no enforced identity; caller is a superuser unless
   PRINCIPALS_JSON is configured. Preserves local dev + existing tests.
- "dev": DB-backed identity via the `X-User-Email` header. The user must exist
   (seed via /admin/bootstrap) and their Membership roles are enforced. This is
   the local stand-in for OIDC and shares the exact same Principal/role checks.
- "oidc": reserved for SSO; resolve the same Principal from a verified token.

Static API keys (API_KEYS) and PRINCIPALS_JSON continue to work in any mode.
"""

import json
import uuid
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import get_db
from app.db.models import Membership, User

ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Reject anonymous requests when API keys are configured (SEC-01)."""
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
    user_id: uuid.UUID | None = None
    email: str | None = None
    # tenant_id -> role ("admin"|"editor"|"viewer"). Unifies static principals
    # and DB memberships.
    memberships: dict = field(default_factory=dict)
    # Optional per-tenant collection allow-list (static PRINCIPALS_JSON only).
    collections: str | set = "*"

    def role_for(self, tenant_id: uuid.UUID) -> str | None:
        return self.memberships.get(tenant_id)

    def authorize(
        self, tenant_id: uuid.UUID, collection_id: uuid.UUID | None = None
    ) -> None:
        if self.superuser:
            return
        if tenant_id not in self.memberships:
            raise HTTPException(status_code=403, detail="Tenant access denied")
        if collection_id is not None and self.collections != "*":
            if collection_id not in self.collections:
                raise HTTPException(status_code=403, detail="Collection access denied")

    def require_role(self, tenant_id: uuid.UUID, min_role: str) -> None:
        """Enforce that the caller has at least `min_role` in the tenant."""
        if self.superuser:
            return
        role = self.memberships.get(tenant_id)
        if role is None:
            raise HTTPException(status_code=403, detail="Tenant access denied")
        if ROLE_RANK.get(role, 0) < ROLE_RANK[min_role]:
            raise HTTPException(
                status_code=403,
                detail=f"Requires {min_role} role (you are {role})",
            )

    def require_admin(self) -> None:
        """Global admin gate (e.g. tenant creation)."""
        if not self.superuser:
            raise HTTPException(status_code=403, detail="Admin privilege required")


def _load_principals() -> dict:
    if not settings.PRINCIPALS_JSON.strip():
        return {}
    return json.loads(settings.PRINCIPALS_JSON)


def _principal_from_static(x_api_key: str | None) -> Principal | None:
    principals = _load_principals()
    if not principals:
        return None
    entry = principals.get(x_api_key or "")
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    collections = entry.get("collections", "*")
    if collections != "*":
        collections = {uuid.UUID(c) for c in collections}
    tenant_id = entry.get("tenant_id")
    memberships = {uuid.UUID(tenant_id): "admin"} if tenant_id else {}
    return Principal(
        superuser=bool(entry.get("superuser", False)),
        memberships=memberships,
        collections=collections,
        email=x_api_key,
    )


def _principal_from_db_user(db: Session, email: str) -> Principal:
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    rows = db.query(Membership).filter(Membership.user_id == user.id).all()
    return Principal(
        superuser=user.is_superuser,
        user_id=user.id,
        email=user.email,
        memberships={m.tenant_id: m.role for m in rows},
    )


def _principal_from_bearer(db: Session, authorization: str | None) -> Principal:
    """Verify an OIDC bearer JWT and resolve it to a Principal (SEC-01)."""
    from app.services import identity
    from app.services.oidc import TokenError, claims_to_identity, get_verifier

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = get_verifier().verify(token)
        email, display_name = claims_to_identity(claims)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        if not settings.OIDC_AUTO_PROVISION:
            raise HTTPException(status_code=401, detail="User not provisioned")
        user = identity.get_or_create_user(db, email, display_name=display_name)
    rows = db.query(Membership).filter(Membership.user_id == user.id).all()
    return Principal(
        superuser=user.is_superuser, user_id=user.id, email=user.email,
        memberships={m.tenant_id: m.role for m in rows},
    )


def get_principal(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> Principal:
    """Resolve the caller into a Principal, honoring AUTH_MODE."""
    # Static principals take precedence in any mode (service accounts).
    static = _principal_from_static(x_api_key)
    if static is not None:
        return static

    if settings.AUTH_MODE == "oidc":
        return _principal_from_bearer(db, authorization)

    if settings.AUTH_MODE == "dev":
        if not x_user_email:
            raise HTTPException(
                status_code=401, detail="Authentication required (X-User-Email)"
            )
        return _principal_from_db_user(db, x_user_email)

    # open mode: superuser (local dev / tests without configured auth).
    return Principal(superuser=True)
