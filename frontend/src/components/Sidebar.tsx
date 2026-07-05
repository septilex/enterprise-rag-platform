import type { ChatSession, Collection } from "../types";
import { roleLabel } from "../lib/format";

export type View = "chat" | "documents" | "activity" | "operations" | "members" | "audit";

const NAV: { view: View; label: string; icon: string; adminOnly?: boolean }[] = [
  { view: "chat", label: "Chat", icon: "◧" },
  { view: "documents", label: "Documents", icon: "▤" },
  { view: "activity", label: "Sources & activity", icon: "◴" },
  { view: "operations", label: "Operations", icon: "◉", adminOnly: true },
  { view: "members", label: "Members", icon: "◍", adminOnly: true },
  { view: "audit", label: "Audit log", icon: "❋", adminOnly: true },
];

export function Sidebar({
  collections,
  activeCollection,
  onSelectCollection,
  view,
  onSelectView,
  isAdmin,
  sessions,
  activeSession,
  onSelectSession,
  onNewSession,
  userEmail,
  userRole,
}: {
  collections: Collection[];
  activeCollection: string | null;
  onSelectCollection: (id: string) => void;
  view: View;
  onSelectView: (v: View) => void;
  isAdmin: boolean;
  sessions: ChatSession[];
  activeSession: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  userEmail: string | null;
  userRole: string | null;
}) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">◈</span>
        <span className="brand-name">Knowledge Base</span>
      </div>

      <div className="sidebar-section">
        <label htmlFor="collection">Workspace collection</label>
        <select id="collection" value={activeCollection ?? ""}
          onChange={(e) => onSelectCollection(e.target.value)}>
          {collections.length === 0 && <option value="">No collections</option>}
          {collections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>

      <nav className="nav">
        {NAV.filter((n) => !n.adminOnly || isAdmin).map((n) => (
          <button key={n.view}
            className={`nav-item ${view === n.view ? "active" : ""}`}
            onClick={() => onSelectView(n.view)}>
            <span className="nav-icon">{n.icon}</span>{n.label}
          </button>
        ))}
      </nav>

      {view === "chat" && (
        <div className="sidebar-section sidebar-sessions">
          <div className="section-head">
            <span>Conversations</span>
            <button className="icon-btn" onClick={onNewSession} title="New conversation">+ New</button>
          </div>
          <ul className="session-list">
            {sessions.map((s) => (
              <li key={s.id}
                className={`session-item ${s.id === activeSession ? "active" : ""}`}
                onClick={() => onSelectSession(s.id)} title={s.title || "Untitled"}>
                <span className="session-dot" />
                <span className="session-title">{s.title || "Untitled"}</span>
              </li>
            ))}
            {sessions.length === 0 && <li className="session-empty">No conversations yet</li>}
          </ul>
        </div>
      )}

      <div className="sidebar-spacer" />
      {(userEmail || userRole) && (
        <div className="user-chip" title={userEmail ?? ""}>
          <span className="user-avatar">{(userEmail ?? "?").charAt(0).toUpperCase()}</span>
          <span className="user-meta">
            <span className="user-email">{userEmail ?? "local dev"}</span>
            {userRole && <span className={`user-role ${userRole}`}>{roleLabel(userRole)}</span>}
          </span>
        </div>
      )}
    </aside>
  );
}
