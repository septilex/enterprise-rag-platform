import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Message } from "./components/Message";
import { Sidebar, type View } from "./components/Sidebar";
import { DocumentsView } from "./views/DocumentsView";
import { ActivityView } from "./views/ActivityView";
import { MembersView } from "./views/MembersView";
import { AuditView } from "./views/AuditView";
import { OperationsView } from "./views/OperationsView";
import type {
  ChatMessage, ChatSession, Citation, Collection, DocumentSummary,
  IngestionRun, Me, Source,
} from "./types";

const TENANT_ID = import.meta.env.VITE_TENANT_ID ?? "";
const USER_ID = import.meta.env.VITE_USER_ID ?? "web-user";

type UploadState =
  | { kind: "idle" }
  | { kind: "uploading"; name: string }
  | { kind: "done"; name: string; chunks: number; quarantined: boolean }
  | { kind: "error"; message: string };

export function App() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionId, setCollectionId] = useState<string | null>(null);
  const [view, setView] = useState<View>("chat");
  const [me, setMe] = useState<Me | null>(null);

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState(true);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upload, setUpload] = useState<UploadState>({ kind: "idle" });

  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [runs, setRuns] = useState<IngestionRun[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [wsLoading, setWsLoading] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const activeCollection = collections.find((c) => c.id === collectionId) ?? null;
  const myRole = me?.superuser ? "admin"
    : me?.tenants.find((t) => t.tenant_id === TENANT_ID)?.role ?? (me?.email ? "viewer" : "admin");
  const isAdmin = myRole === "admin";
  const canEdit = myRole === "admin" || myRole === "editor";

  useEffect(() => {
    api.getMe().then(setMe).catch(() => setMe(null));
  }, []);

  useEffect(() => {
    if (!TENANT_ID) return;
    api.listSessions(TENANT_ID, USER_ID).then(setSessions).catch(() => {});
    api.listCollections(TENANT_ID).then((cols) => {
      setCollections(cols);
      if (cols.length) setCollectionId((cur) => cur ?? cols[0].id);
    }).catch(() => {});
  }, []);

  const loadWorkspace = useCallback((cid: string) => {
    setWsLoading(true);
    Promise.allSettled([
      api.listDocuments(TENANT_ID, cid).then(setDocuments),
      api.listIngestionRuns(TENANT_ID, cid, 15).then(setRuns),
      api.listSources(TENANT_ID, cid).then(setSources),
    ]).finally(() => setWsLoading(false));
  }, []);

  useEffect(() => {
    if (!TENANT_ID || !collectionId) { setDocuments([]); setRuns([]); setSources([]); return; }
    loadWorkspace(collectionId);
  }, [collectionId, loadWorkspace]);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages]);

  const loadSession = useCallback(async (id: string) => {
    setDraft(false); setSessionId(id); setError(null); setMessages([]);
    try {
      const history = await api.sessionMessages(TENANT_ID, id);
      setMessages(history.map((m) => ({ ...m, citations: m.citations ?? [] })));
    } catch (e) { setError(String(e)); }
  }, []);

  const newSession = useCallback(() => {
    setDraft(true); setSessionId(null); setMessages([]); setError(null); setView("chat");
  }, []);

  const send = useCallback(async () => {
    const query = input.trim();
    if (!query || !collectionId || busy) return;
    setError(null); setInput(""); setBusy(true);

    let sid = sessionId;
    if (!sid) {
      const s = await api.createSession(TENANT_ID, USER_ID, collectionId, query.slice(0, 48));
      setSessions((prev) => [s, ...prev]); sid = s.id; setSessionId(s.id); setDraft(false);
    }

    setMessages((prev) => [
      ...prev,
      { role: "user", content: query, citations: [] },
      { role: "assistant", content: "", citations: [], streaming: true },
    ]);
    const patchLast = (patch: Partial<ChatMessage>) =>
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = { ...next[next.length - 1], ...patch };
        return next;
      });

    let citations: Citation[] = [];
    let text = "";
    await api.streamChat(
      { tenantId: TENANT_ID, collectionId, query, sessionId: sid },
      {
        onCitations: (c) => { citations = c; patchLast({ citations }); },
        onToken: (t) => { text += t; patchLast({ content: text }); },
        onDone: (grounded, message) =>
          patchLast({ streaming: false, grounded, content: text || message || "" }),
        onError: (e) => { setError(e); patchLast({ streaming: false }); },
      },
    );
    setBusy(false);
  }, [input, collectionId, sessionId, busy]);

  const sendFeedback = useCallback(async (msgIdx: number, rating: "up" | "down") => {
    const msg = messages[msgIdx]; const userMsg = messages[msgIdx - 1];
    if (!collectionId || !msg) return;
    await api.submitFeedback({
      tenantId: TENANT_ID, collectionId, query: userMsg?.content ?? "",
      answer: msg.content, rating, chunkIds: msg.citations.map((c) => c.chunk_id),
    });
  }, [messages, collectionId]);

  const uploadFile = useCallback(async (file: File) => {
    if (!collectionId) return;
    setError(null); setUpload({ kind: "uploading", name: file.name });
    try {
      const res = await api.uploadFile({ tenantId: TENANT_ID, collectionId, sessionId, file });
      setUpload({ kind: "done", name: file.name, chunks: res.chunks_created,
                  quarantined: res.status === "quarantined" });
      loadWorkspace(collectionId);
      setTimeout(() => setUpload((u) => (u.kind === "done" ? { kind: "idle" } : u)), 6000);
    } catch (e) {
      setUpload({ kind: "error", message: String(e) });
    }
  }, [collectionId, sessionId, loadWorkspace]);

  const toggleSource = useCallback(async (s: Source) => {
    await api.setSourceEnabled(TENANT_ID, s.id, !s.enabled);
    if (collectionId) loadWorkspace(collectionId);
  }, [collectionId, loadWorkspace]);

  const reindexSource = useCallback(async (s: Source) => {
    await api.reindexSource(TENANT_ID, s.id);
    if (collectionId) loadWorkspace(collectionId);
  }, [collectionId, loadWorkspace]);

  return (
    <div className="app">
      <Sidebar
        collections={collections}
        activeCollection={collectionId}
        onSelectCollection={setCollectionId}
        view={view}
        onSelectView={setView}
        isAdmin={isAdmin}
        sessions={sessions}
        activeSession={sessionId}
        onSelectSession={(id) => { setView("chat"); loadSession(id); }}
        onNewSession={newSession}
        userEmail={me?.email ?? null}
        userRole={me?.email ? myRole : null}
      />

      <input ref={fileRef} type="file" hidden
        onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadFile(f); e.target.value = ""; }} />

      {view === "chat" && (
        <ChatMain
          collectionName={activeCollection?.name ?? null}
          documentsCount={documents.length}
          draft={draft} messages={messages} input={input} busy={busy}
          error={error} upload={upload} canEdit={canEdit} scrollRef={scrollRef}
          onInput={setInput} onSend={send} onFeedback={sendFeedback}
          onAttach={() => fileRef.current?.click()}
          onDismissError={() => setError(null)}
          onDismissUpload={() => setUpload({ kind: "idle" })}
          onCreateCollection={canEdit ? (c) => { setCollections((p) => [...p, c]); setCollectionId(c.id); } : undefined}
        />
      )}

      {view === "documents" && (
        <main className="workspace">
          <DocumentsView collectionName={activeCollection?.name ?? null}
            documents={documents} loading={wsLoading} canEdit={canEdit}
            onUpload={() => fileRef.current?.click()} />
        </main>
      )}
      {view === "activity" && (
        <main className="workspace">
          <ActivityView collectionName={activeCollection?.name ?? null}
            sources={sources} runs={runs} loading={wsLoading} canEdit={canEdit}
            onToggleSource={toggleSource} onReindex={reindexSource} />
        </main>
      )}
      {view === "operations" && (
        <main className="workspace"><OperationsView tenantId={TENANT_ID} isAdmin={isAdmin} /></main>
      )}
      {view === "members" && (
        <main className="workspace"><MembersView tenantId={TENANT_ID} isAdmin={isAdmin} /></main>
      )}
      {view === "audit" && (
        <main className="workspace"><AuditView tenantId={TENANT_ID} isAdmin={isAdmin} /></main>
      )}
    </div>
  );
}

function ChatMain({
  collectionName, documentsCount, draft, messages, input, busy, error, upload,
  canEdit, scrollRef, onInput, onSend, onFeedback, onAttach, onDismissError,
  onDismissUpload, onCreateCollection,
}: {
  collectionName: string | null; documentsCount: number; draft: boolean;
  messages: ChatMessage[]; input: string; busy: boolean; error: string | null;
  upload: UploadState; canEdit: boolean;
  scrollRef: React.RefObject<HTMLDivElement>;
  onInput: (v: string) => void; onSend: () => void;
  onFeedback: (i: number, r: "up" | "down") => void; onAttach: () => void;
  onDismissError: () => void; onDismissUpload: () => void;
  onCreateCollection?: (c: Collection) => void;
}) {
  return (
    <main className="chat">
      <header className="chat-header">
        <div className="header-title">
          <h1>{collectionName ?? "Knowledge Base"}</h1>
          <p className="header-sub">
            {collectionName
              ? `${documentsCount} document${documentsCount === 1 ? "" : "s"} indexed`
              : "Grounded answers from your document collections"}
          </p>
        </div>
        {!TENANT_ID && <span className="warn">Set VITE_TENANT_ID to connect.</span>}
        <div className="header-right">
          {onCreateCollection && <CollectionCreator onCreated={onCreateCollection} />}
        </div>
      </header>

      <div className="messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="empty">
            <div className="empty-mark">◈</div>
            {!collectionName ? (
              <><h2>Select a collection</h2><p>Choose a collection on the left to get started.</p></>
            ) : draft ? (
              <><h2>New conversation</h2><p>Ask a question — answers are grounded in “{collectionName}” with cited sources.</p></>
            ) : (
              <><h2>{collectionName}</h2><p>This conversation has no messages yet. Ask a question to continue.</p></>
            )}
          </div>
        )}
        {messages.map((m, i) => (
          <Message key={i} message={m}
            onFeedback={m.role === "assistant" ? (r) => onFeedback(i, r) : undefined} />
        ))}
      </div>

      {upload.kind !== "idle" && <UploadBanner upload={upload} onDismiss={onDismissUpload} />}
      {error && <div className="error-bar"><span>⚠ {error}</span><button onClick={onDismissError}>Dismiss</button></div>}

      <form className="composer" onSubmit={(e) => { e.preventDefault(); onSend(); }}>
        {canEdit && (
          <button type="button" className="attach" title="Upload a document"
            disabled={!collectionName || upload.kind === "uploading"} onClick={onAttach}>
            {upload.kind === "uploading" ? <span className="spin" /> : "＋"}
          </button>
        )}
        <input className="composer-input" value={input} onChange={(e) => onInput(e.target.value)}
          placeholder={collectionName ? "Ask a question…" : "Select a collection first"}
          disabled={!collectionName || busy} />
        <button className="send" type="submit" disabled={!collectionName || busy || !input.trim()}>
          {busy ? "…" : "Send"}
        </button>
      </form>
    </main>
  );
}

function UploadBanner({ upload, onDismiss }: { upload: UploadState; onDismiss: () => void }) {
  if (upload.kind === "uploading")
    return <div className="banner info"><span className="spin" /> Uploading {upload.name}…</div>;
  if (upload.kind === "error")
    return <div className="banner bad"><span>Upload failed: {upload.message}</span><button onClick={onDismiss}>Dismiss</button></div>;
  if (upload.kind === "done")
    return (
      <div className={`banner ${upload.quarantined ? "warn2" : "ok"}`}>
        <span>{upload.quarantined
          ? `${upload.name} could not be parsed and was quarantined.`
          : `${upload.name} indexed — ${upload.chunks} chunk${upload.chunks === 1 ? "" : "s"}.`}</span>
        <button onClick={onDismiss}>Dismiss</button>
      </div>
    );
  return null;
}

function CollectionCreator({ onCreated }: { onCreated: (c: Collection) => void }) {
  const [name, setName] = useState("");
  const [open, setOpen] = useState(false);
  if (!open) return <button className="new-collection-btn" onClick={() => setOpen(true)}>+ Collection</button>;
  return (
    <form className="collection-creator" onSubmit={async (e) => {
      e.preventDefault();
      if (!name.trim()) return;
      const c = await api.createCollection(TENANT_ID, name.trim());
      onCreated(c); setName(""); setOpen(false);
    }}>
      <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
        onBlur={() => !name && setOpen(false)} placeholder="Collection name" />
      <button type="submit">Create</button>
    </form>
  );
}
