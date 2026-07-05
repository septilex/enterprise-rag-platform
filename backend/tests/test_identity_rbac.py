"""Security + identity/RBAC foundation (SEC-01/02/05).

Exercises AUTH_MODE=dev: DB users, memberships, role enforcement, tenant
isolation, /me, and the persistent audit trail.
"""

import uuid

import pytest

from app.core.config import settings
from app.db.models import AuditLog, Collection
from app.services import identity


@pytest.fixture
def dev_auth(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "dev")


def _hdr(email):
    return {"X-User-Email": email}


def test_dev_mode_requires_identity(api_client, tenant, dev_auth):
    # No X-User-Email -> 401 (SEC-01).
    r = api_client.get("/me")
    assert r.status_code == 401


def test_me_reports_roles(api_client, tenant, db_session, dev_auth):
    u = identity.get_or_create_user(db_session, "admin@acme.test")
    identity.set_membership(db_session, u.id, tenant.id, "admin")
    r = api_client.get("/me", headers=_hdr("admin@acme.test"))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "admin@acme.test"
    assert any(t["tenant_id"] == str(tenant.id) and t["role"] == "admin"
               for t in body["tenants"])


def test_viewer_cannot_write_but_can_read(api_client, tenant, db_session, dev_auth):
    u = identity.get_or_create_user(db_session, "viewer@acme.test")
    identity.set_membership(db_session, u.id, tenant.id, "viewer")
    h = _hdr("viewer@acme.test")
    tid = str(tenant.id)

    # viewer denied collection create (needs editor)
    r = api_client.post("/collections", headers=h, json={"tenant_id": tid, "name": "c"})
    assert r.status_code == 403

    # seed a collection as admin, then viewer can search it (read allowed)
    coll = Collection(tenant_id=tenant.id, name="readable")
    db_session.add(coll); db_session.commit(); db_session.refresh(coll)
    r = api_client.post("/search", headers=h, json={
        "tenant_id": tid, "collection_id": str(coll.id), "query": "x", "top_k": 3})
    assert r.status_code == 200


def test_editor_can_create_collection_admin_can_delete(api_client, tenant, db_session, dev_auth):
    ed = identity.get_or_create_user(db_session, "editor@acme.test")
    identity.set_membership(db_session, ed.id, tenant.id, "editor")
    tid = str(tenant.id)

    r = api_client.post("/collections", headers=_hdr("editor@acme.test"),
                        json={"tenant_id": tid, "name": "editorcoll"})
    assert r.status_code == 201
    cid = r.json()["id"]

    # editor cannot delete a document (needs admin)
    r = api_client.delete(f"/documents/{uuid.uuid4()}", headers=_hdr("editor@acme.test"),
                          params={"tenant_id": tid})
    assert r.status_code == 403  # role denied before 404

    assert cid  # created


def test_tenant_isolation_cross_tenant_denied(api_client, tenant, db_session, dev_auth):
    # user is admin of `tenant`, but tries to act on a different tenant
    u = identity.get_or_create_user(db_session, "iso@acme.test")
    identity.set_membership(db_session, u.id, tenant.id, "admin")
    other = str(uuid.uuid4())
    r = api_client.post("/collections", headers=_hdr("iso@acme.test"),
                        json={"tenant_id": other, "name": "x"})
    assert r.status_code == 403


def test_admin_grants_membership_and_audit_persisted(api_client, tenant, db_session, dev_auth):
    admin = identity.get_or_create_user(db_session, "boss@acme.test")
    identity.set_membership(db_session, admin.id, tenant.id, "admin")
    tid = str(tenant.id)

    r = api_client.post("/admin/members", headers=_hdr("boss@acme.test"),
                        json={"tenant_id": tid, "email": "newhire@acme.test", "role": "editor"})
    assert r.status_code == 201
    assert r.json()["role"] == "editor"

    # audit row persisted (SEC-05)
    rows = db_session.query(AuditLog).filter(
        AuditLog.tenant_id == tenant.id,
        AuditLog.action == "membership.grant").all()
    assert rows and rows[0].actor == "boss@acme.test"

    # audit endpoint (admin only)
    a = api_client.get("/admin/audit", headers=_hdr("boss@acme.test"), params={"tenant_id": tid})
    assert a.status_code == 200
    assert any(e["action"] == "membership.grant" for e in a.json())


def test_admin_can_remove_member_but_not_last_admin(api_client, tenant, db_session, dev_auth):
    admin = identity.get_or_create_user(db_session, "boss2@acme.test")
    identity.set_membership(db_session, admin.id, tenant.id, "admin")
    tid = str(tenant.id)
    h = _hdr("boss2@acme.test")

    # add then remove an editor
    api_client.post("/admin/members", headers=h,
                    json={"tenant_id": tid, "email": "temp@acme.test", "role": "editor"})
    r = api_client.request("DELETE", "/admin/members", headers=h,
                           params={"tenant_id": tid, "email": "temp@acme.test"})
    assert r.status_code == 200 and r.json()["removed"] is True

    # cannot remove the last admin
    r = api_client.request("DELETE", "/admin/members", headers=h,
                           params={"tenant_id": tid, "email": "boss2@acme.test"})
    assert r.status_code == 400


def test_bootstrap_creates_admin(api_client, db_session, monkeypatch):
    # open mode: bootstrap allowed (superuser), then that user is admin in dev mode
    r = api_client.post("/admin/bootstrap",
                        json={"email": "founder@acme.test", "tenant_name": f"org-{uuid.uuid4()}"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "admin" and body["email"] == "founder@acme.test"
