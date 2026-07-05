import { useEffect, useState } from "react";
import { api } from "../api";
import type { Member } from "../types";
import { Placeholder } from "./DocumentsView";
import { roleLabel } from "../lib/format";

const ROLES = ["viewer", "editor", "admin"];

// Workspace access management (SEC-02). Admin-only; RBAC enforced server-side.
export function MembersView({ tenantId, isAdmin }: { tenantId: string; isAdmin: boolean }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [busy, setBusy] = useState(false);

  const load = () => {
    setLoading(true);
    api.listMembers(tenantId)
      .then(setMembers).catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { if (isAdmin) load(); else setLoading(false); }, [tenantId, isAdmin]);

  if (!isAdmin) {
    return <Placeholder title="Admins only"
      body="You need the Admin role in this workspace to manage members." />;
  }

  const grant = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy(true); setError(null);
    try { await api.grantMember(tenantId, email.trim(), role); setEmail(""); load(); }
    catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  };

  const changeRole = async (m: Member, newRole: string) => {
    setError(null);
    try { await api.grantMember(tenantId, m.email, newRole); load(); }
    catch (err) { setError(String(err)); }
  };

  const remove = async (m: Member) => {
    setError(null);
    try { await api.removeMember(tenantId, m.email); load(); }
    catch (err) { setError(String(err)); }
  };

  return (
    <div className="view">
      <div className="view-head">
        <div><h2>Members & access</h2><p>{members.length} member{members.length === 1 ? "" : "s"} in this workspace</p></div>
      </div>

      <form className="inline-form" onSubmit={grant}>
        <input type="email" placeholder="person@company.com" value={email}
          onChange={(e) => setEmail(e.target.value)} />
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
        </select>
        <button className="btn-primary" type="submit" disabled={busy || !email.trim()}>Add member</button>
      </form>

      {error && <div className="error-inline">{error}</div>}
      {loading && <p className="muted-block">Loading…</p>}

      {members.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Member</th><th>Role</th><th></th></tr></thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.user_id}>
                  <td className="cell-strong">{m.email}</td>
                  <td>
                    <select className="role-select" value={m.role}
                      onChange={(e) => changeRole(m, e.target.value)}>
                      {ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
                    </select>
                  </td>
                  <td className="cell-actions">
                    <button className="btn-ghost danger" onClick={() => remove(m)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
