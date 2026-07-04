// REST + SSE client for the RAG Platform backend (UI-06).
// Base URL and API key come from Vite env; the API key satisfies SEC-01.
import type { ChatMessage, ChatSession, Citation, Collection } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
const API_KEY = import.meta.env.VITE_API_KEY ?? "";

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
  if (API_KEY) h["X-API-Key"] = API_KEY;
  return h;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  async createTenant(name: string): Promise<{ id: string }> {
    return json(await fetch(`${BASE}/tenants`, {
      method: "POST", headers: headers(), body: JSON.stringify({ name }),
    }));
  },

  async listCollections(tenantId: string): Promise<Collection[]> {
    const q = new URLSearchParams({ tenant_id: tenantId });
    return json(await fetch(`${BASE}/collections?${q}`, { headers: headers() }));
  },

  async createCollection(tenantId: string, name: string): Promise<Collection> {
    return json(await fetch(`${BASE}/collections`, {
      method: "POST", headers: headers(),
      body: JSON.stringify({ tenant_id: tenantId, name }),
    }));
  },

  async listSessions(tenantId: string, userId: string): Promise<ChatSession[]> {
    const q = new URLSearchParams({ tenant_id: tenantId, user_id: userId });
    return json(await fetch(`${BASE}/sessions?${q}`, { headers: headers() }));
  },

  async createSession(
    tenantId: string, userId: string, collectionId: string | null, title: string,
  ): Promise<ChatSession> {
    return json(await fetch(`${BASE}/sessions`, {
      method: "POST", headers: headers(),
      body: JSON.stringify({
        tenant_id: tenantId, user_id: userId,
        collection_id: collectionId, title,
      }),
    }));
  },

  async sessionMessages(tenantId: string, sessionId: string): Promise<ChatMessage[]> {
    const q = new URLSearchParams({ tenant_id: tenantId });
    return json(await fetch(`${BASE}/sessions/${sessionId}/messages?${q}`, {
      headers: headers(),
    }));
  },

  // UI-07: upload a file as retrieval context for the current collection.
  async uploadFile(input: {
    tenantId: string; collectionId: string; sessionId?: string | null; file: File;
  }): Promise<{ document_id: string; status: string; chunks_created: number }> {
    const form = new FormData();
    form.append("tenant_id", input.tenantId);
    form.append("collection_id", input.collectionId);
    if (input.sessionId) form.append("session_id", input.sessionId);
    form.append("file", input.file);
    const h: Record<string, string> = {};
    if (API_KEY) h["X-API-Key"] = API_KEY;
    return json(await fetch(`${BASE}/documents/upload`, {
      method: "POST", headers: h, body: form,
    }));
  },

  async submitFeedback(input: {
    tenantId: string; collectionId: string; query: string; answer: string;
    rating: "up" | "down"; comment?: string; chunkIds: string[];
  }): Promise<void> {
    await fetch(`${BASE}/feedback`, {
      method: "POST", headers: headers(),
      body: JSON.stringify({
        tenant_id: input.tenantId, collection_id: input.collectionId,
        query: input.query, answer: input.answer, rating: input.rating,
        comment: input.comment, chunk_ids: input.chunkIds,
      }),
    });
  },

  // Streaming chat over SSE (UI-01). EventSource cannot POST, so we stream the
  // fetch body and parse `data:` lines ourselves.
  async streamChat(
    input: {
      tenantId: string; collectionId: string; query: string;
      sessionId?: string | null;
    },
    handlers: {
      onCitations: (c: Citation[]) => void;
      onToken: (t: string) => void;
      onDone: (grounded: boolean, message?: string) => void;
      onError: (e: string) => void;
    },
  ): Promise<void> {
    const res = await fetch(`${BASE}/chat/stream`, {
      method: "POST", headers: headers(),
      body: JSON.stringify({
        tenant_id: input.tenantId, collection_id: input.collectionId,
        query: input.query, session_id: input.sessionId ?? null,
      }),
    });
    if (!res.ok || !res.body) {
      handlers.onError(`${res.status} ${await res.text()}`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        const evt = JSON.parse(line.slice(5).trim());
        if (evt.type === "citations") handlers.onCitations(evt.citations);
        else if (evt.type === "token") handlers.onToken(evt.text);
        else if (evt.type === "done") handlers.onDone(evt.grounded, evt.message);
      }
    }
  },
};
