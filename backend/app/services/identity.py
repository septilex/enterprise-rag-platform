"""Identity, membership (RBAC) and persistent audit services (SEC-01/02/05)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import AuditLog, Membership, Tenant, User

VALID_ROLES = {"admin", "editor", "viewer"}


def record_audit(
    db: Session,
    tenant_id: uuid.UUID | None,
    actor: str,
    action: str,
    target: dict | None = None,
) -> AuditLog:
    """Append an immutable audit row (SEC-05). Never updated/deleted."""
    row = AuditLog(tenant_id=tenant_id, actor=actor, action=action, target=target or {})
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_audit(
    db: Session, tenant_id: uuid.UUID, limit: int = 200
) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(limit, 1000))
        .all()
    )


def get_or_create_user(
    db: Session, email: str, display_name: str = "", is_superuser: bool = False
) -> User:
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        user = User(email=email, display_name=display_name or email, is_superuser=is_superuser)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def set_membership(
    db: Session, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
) -> Membership:
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role: {role}")
    m = (
        db.query(Membership)
        .filter(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
        .one_or_none()
    )
    if m is None:
        m = Membership(user_id=user_id, tenant_id=tenant_id, role=role)
        db.add(m)
    else:
        m.role = role
    db.commit()
    db.refresh(m)
    return m


def remove_membership(db: Session, tenant_id: uuid.UUID, email: str) -> bool:
    """Revoke a user's membership in a tenant. Returns False if not found."""
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        return False
    m = (
        db.query(Membership)
        .filter(Membership.user_id == user.id, Membership.tenant_id == tenant_id)
        .one_or_none()
    )
    if m is None:
        return False
    db.delete(m)
    db.commit()
    return True


def count_admins(db: Session, tenant_id: uuid.UUID) -> int:
    return (
        db.query(Membership)
        .filter(Membership.tenant_id == tenant_id, Membership.role == "admin")
        .count()
    )


def list_members(db: Session, tenant_id: uuid.UUID) -> list[tuple[User, Membership]]:
    rows = (
        db.query(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .filter(Membership.tenant_id == tenant_id)
        .order_by(User.email.asc())
        .all()
    )
    return rows


def bootstrap_admin(db: Session, email: str, tenant_name: str) -> dict:
    """Idempotently create a tenant + admin user + membership (dev bootstrap).

    Returns identifiers the caller can use to log in via X-User-Email.
    """
    tenant = db.query(Tenant).filter(Tenant.name == tenant_name).one_or_none()
    if tenant is None:
        tenant = Tenant(name=tenant_name)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
    user = get_or_create_user(db, email, display_name=email, is_superuser=False)
    set_membership(db, user.id, tenant.id, "admin")
    record_audit(db, tenant.id, email, "bootstrap.admin",
                 {"tenant": tenant_name, "user": email})
    return {"tenant_id": str(tenant.id), "tenant_name": tenant.name,
            "user_id": str(user.id), "email": user.email, "role": "admin"}
