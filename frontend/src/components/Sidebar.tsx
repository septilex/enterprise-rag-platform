import type { ChatSession, Collection } from "../types";

// UI-03 session list + UI-04 collection scoping selector.
export function Sidebar({
  collections,
  activeCollection,
  onSelectCollection,
  sessions,
  activeSession,
  onSelectSession,
  onNewSession,
}: {
  collections: Collection[];
  activeCollection: string | null;
  onSelectCollection: (id: string) => void;
  sessions: ChatSession[];
  activeSession: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar-section">
        <label htmlFor="collection">Collection</label>
        <select
          id="collection"
          value={activeCollection ?? ""}
          onChange={(e) => onSelectCollection(e.target.value)}
        >
          {collections.length === 0 && <option value="">(none)</option>}
          {collections.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>

      <div className="sidebar-section sidebar-sessions">
        <div className="sidebar-sessions-head">
          <span>Conversations</span>
          <button onClick={onNewSession} title="New conversation">＋</button>
        </div>
        <ul>
          {sessions.map((s) => (
            <li
              key={s.id}
              className={s.id === activeSession ? "active" : ""}
              onClick={() => onSelectSession(s.id)}
            >
              {s.title || "Untitled"}
            </li>
          ))}
          {sessions.length === 0 && <li className="muted">No conversations yet</li>}
        </ul>
      </div>
    </aside>
  );
}
