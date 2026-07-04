import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Message } from "./components/Message";
import { Sidebar } from "./components/Sidebar";
import type { ChatMessage, ChatSession, Citation, Collection } from "./types";

// Config surfaced via Vite env; in a real deployment these come from an
// authenticated session. Kept explicit here so the demo is self-contained.
const TENANT_ID = import.meta.env.VITE_TENANT_ID ?? "";
const USER_ID = import.meta.env.VITE_USER_ID ?? "web-user";

export function App() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionId, setCollectionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!TENANT_ID) return;
    api.listSessions(TENANT_ID, USER_ID).then(setSessions).catch(() => {});
    // Load existing collections so the dropdown is populated on reload (not just
    // ones created in-session), and default-select the first.
    api.listCollections(TENANT_ID).then((cols) => {
      setCollections(cols);
      if (cols.length) setCollectionId((cur) => cur ?? cols[0].id);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages]);

  // UI-03: restore a prior conversation thread when a session is selected.
  const loadSession = useCallback(async (id: string) => {
    setSessionId(id);
    try {
      const history = await api.sessionMessages(TENANT_ID, id);
      setMessages(history.map((m) => ({ ...m, citations: m.citations ?? [] })));
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const newSession = useCallback(async () => {
    if (!TENANT_ID) return;
    const s = await api.createSession(
      TENANT_ID, USER_ID, collectionId, "New conversation",
    );
    setSessions((prev) => [s, ...prev]);
    setSessionId(s.id);
    setMessages([]);
  }, [collectionId]);

  const send = useCallback(async () => {
    const query = input.trim();
    if (!query || !collectionId || busy) return;
    setError(null);
    setInput("");
    setBusy(true);

    let sid = sessionId;
    if (!sid) {
      const s = await api.createSession(
        TENANT_ID, USER_ID, collectionId, query.slice(0, 40),
      );
      setSessions((prev) => [s, ...prev]);
      sid = s.id;
      setSessionId(s.id);
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
          patchLast({
            streaming: false,
            grounded,
            content: text || message || "",
          }),
        onError: (e) => { setError(e); patchLast({ streaming: false }); },
      },
    );
    setBusy(false);
  }, [input, collectionId, sessionId, busy]);

  const sendFeedback = useCallback(
    async (msgIdx: number, rating: "up" | "down") => {
      const msg = messages[msgIdx];
      const userMsg = messages[msgIdx - 1];
      if (!collectionId || !msg) return;
      await api.submitFeedback({
        tenantId: TENANT_ID, collectionId,
        query: userMsg?.content ?? "", answer: msg.content, rating,
        chunkIds: msg.citations.map((c) => c.chunk_id),
      });
    },
    [messages, collectionId],
  );

  // UI-07: upload a file into the active collection as retrieval context.
  const uploadFile = useCallback(
    async (file: File) => {
      if (!collectionId) return;
      setError(null);
      setNotice(`Uploading ${file.name}…`);
      try {
        const res = await api.uploadFile({
          tenantId: TENANT_ID, collectionId, sessionId, file,
        });
        setNotice(
          res.status === "quarantined"
            ? `⚠️ ${file.name} could not be parsed (quarantined).`
            : `✓ ${file.name} indexed (${res.chunks_created} chunks).`,
        );
      } catch (e) {
        setError(`Upload failed: ${String(e)}`);
        setNotice(null);
      }
    },
    [collectionId, sessionId],
  );

  return (
    <div className="app">
      <Sidebar
        collections={collections}
        activeCollection={collectionId}
        onSelectCollection={setCollectionId}
        sessions={sessions}
        activeSession={sessionId}
        onSelectSession={loadSession}
        onNewSession={newSession}
      />

      <main className="chat">
        <header className="chat-header">
          <h1>RAG Platform</h1>
          {!TENANT_ID && (
            <span className="warn">Set VITE_TENANT_ID to connect.</span>
          )}
          <CollectionCreator
            tenantId={TENANT_ID}
            onCreated={(c) => { setCollections((p) => [...p, c]); setCollectionId(c.id); }}
          />
        </header>

        <div className="messages" ref={scrollRef}>
          {messages.map((m, i) => (
            <Message
              key={i}
              message={m}
              onFeedback={m.role === "assistant" ? (r) => sendFeedback(i, r) : undefined}
            />
          ))}
          {messages.length === 0 && (
            <div className="empty">Ask a question grounded in your collection.</div>
          )}
        </div>

        {notice && <div className="notice-bar">{notice}</div>}
        {error && <div className="error-bar">{error}</div>}

        <form
          className="composer"
          onSubmit={(e) => { e.preventDefault(); void send(); }}
        >
          <input
            ref={fileRef}
            type="file"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void uploadFile(f);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            className="attach"
            title="Upload a file as context"
            disabled={!collectionId || busy}
            onClick={() => fileRef.current?.click()}
          >
            📎
          </button>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={collectionId ? "Ask a question…" : "Select a collection first"}
            disabled={!collectionId || busy}
          />
          <button type="submit" disabled={!collectionId || busy || !input.trim()}>
            {busy ? "…" : "Send"}
          </button>
        </form>
      </main>
    </div>
  );
}

// Minimal inline collection creation so the demo is usable without curl.
function CollectionCreator({
  tenantId,
  onCreated,
}: {
  tenantId: string;
  onCreated: (c: Collection) => void;
}) {
  const [name, setName] = useState("");
  if (!tenantId) return null;
  return (
    <form
      className="collection-creator"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!name.trim()) return;
        const c = await api.createCollection(tenantId, name.trim());
        onCreated(c);
        setName("");
      }}
    >
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="new collection"
      />
      <button type="submit">Add</button>
    </form>
  );
}
