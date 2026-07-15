# RAG Platform — User Guide

A walkthrough of every screen and control in the web app, written for the
people who will use it day to day. No prior knowledge assumed.

**What this platform does:** you upload your organisation's documents into
*collections*, and then ask questions in plain language. Answers are generated
**only from your documents** — every answer carries numbered citations
pointing at the exact passages it came from. If the documents don't contain an
answer, it says so instead of guessing.

---

## 1. The layout

The app has two areas:

- **Left sidebar** (fixed): workspace switcher, navigation, and your
  conversation history.
- **Main panel**: whichever view you selected — Chat by default.

### Workspace collection (top of sidebar)

A **collection** is an isolated set of documents — think "HR Policies",
"Engineering Runbooks", "Finance". The dropdown switches which collection you
are working in. Everything below it (chat answers, document lists, activity)
is scoped to the selected collection. Questions asked in one collection never
use documents from another.

Admins/editors can create a new collection with the **+ Collection** button in
the chat header.

---

## 2. Chat (default view)

Ask a question in the box at the bottom and press **Send**.

- **Streaming answers** — the reply appears word by word as it's generated,
  like typing. You don't wait for the whole answer.
- **Citations** — under each answer you'll see "GROUNDED IN N SOURCES" with
  numbered chips (`[1]`, `[2]`…). Each chip names the document and chunk the
  answer used. The `[1]`-style markers inside the answer text refer to these.
- **"No grounded answer"** — if nothing in the collection is relevant enough,
  you get an amber banner saying so. That is deliberate: the system refuses to
  invent an answer that isn't supported by your documents. Rephrase the
  question or upload the relevant document.
- **👍 / 👎 feedback** — rate any answer. Ratings are stored and feed the
  quality-evaluation dataset admins can review; downvoted answers are how the
  team spots bad retrievals.
- **Conversations** — every chat is a session, listed in the sidebar under
  CONVERSATIONS. Click one to reopen its full history (it survives page
  reloads); **+ New** starts a fresh thread. Titles are taken from your first
  question.

### Uploading documents (＋ button, left of the question box)

Editors and admins can add a document straight from chat:

- **Small files** index in a couple of seconds; a green banner confirms
  "*indexed — N chunks*".
- **Large files (over ~1 MB)** are handed to a background worker: you'll see
  "*Indexing … in the background — you can keep chatting*". The UI stays fully
  usable; when indexing finishes the banner updates on its own. Progress is
  also visible under **Sources & activity**.
- **Unreadable files** (scanned/image-only PDFs, corrupt files) are
  **quarantined**, with a banner telling you so. Scanned PDFs have no text
  layer to index — if you can't select text in the PDF, the platform can't
  read it either (OCR is not performed).

Supported inputs: text-based PDFs, and any text format (`.txt`, `.md`, `.csv`,
logs, exports…).

---

## 3. Documents

The library of everything indexed in the current collection: filename, status,
chunk count, and when it was added.

Statuses you may see:

| Status | Meaning |
|---|---|
| `embedded` | Fully indexed and searchable — the normal end state. |
| `chunked` / `pending` | Mid-ingestion (or an interrupted run — re-uploading repairs it). |
| `quarantined` | Could not be parsed; it is **not** searchable. The reason is recorded. |

---

## 4. Sources & activity

Two things live here:

- **Sources** — where documents come from. Manual uploads are one source;
  admins can also register **connector** sources (S3 buckets, Confluence
  spaces) that sync on a schedule or on demand. Each source can be
  enabled/disabled, re-synced, or fully re-indexed from here.
- **Ingestion runs** — the audit trail of every indexing job: trigger
  (manual/scheduled/webhook), status (queued → running → succeeded/failed),
  documents seen/indexed/quarantined, chunks created, and the error message if
  something failed. This is the first place to look when an upload doesn't
  show up.

Failed runs can be retried; retries are safe — re-ingesting an unchanged
document never duplicates anything.

---

## 5. Operations (admins)

Live health of the platform:

- **Worker status** — whether the background ingestion worker is alive
  (heartbeat) and the queue depths (incremental / bulk / dead-letter). A
  growing queue with a dead worker is the "uploads stuck" signature.
- **Ingestion by status** — run counts, failures.
- **Dead-letter queue** — jobs that failed all retries, kept visible rather
  than dropped.

---

## 6. Members (admins)

Who can do what, per tenant:

| Role | Can |
|---|---|
| **viewer** | Ask questions, read documents. |
| **editor** | Everything viewers can, plus upload documents and manage sources. |
| **admin** | Everything, plus members, operations, audit log. |

Grant or remove access by email here. With SSO (OIDC) enabled, sign-in is via
your company identity provider and these roles apply on top.

---

## 7. Audit log (admins)

An append-only record of administrative actions — collection created, document
uploaded/erased, member granted, source changed — with who did it and when.

---

## 8. Practical tips

- **Scope questions to the right collection first** — the most common cause of
  "no grounded answer" is asking in the wrong collection.
- **Phrase questions with the document's vocabulary** when possible; exact
  terms (form numbers, product names) retrieve especially well because search
  is *hybrid* — it matches by meaning **and** by keyword.
- **Repeat questions are instant** — identical/similar questions are served
  from cache.
- **Big uploads**: drop the file, keep working. Check Sources & activity if
  you're curious; the banner will tell you when it's done.
- **Something looks wrong?** Thumbs-down the answer with the issue — that's
  the signal the maintainers act on.

---

## 9. For developers: using it without the UI

Everything the UI does is a documented REST/SSE API (see `/docs` on the
backend for interactive OpenAPI):

```bash
# Ask a question (full JSON answer with citations)
curl -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"tenant_id":"…","collection_id":"…","query":"…"}'

# Streamed answer (SSE tokens, then citations)
curl -N -X POST $BASE/chat/stream -H 'Content-Type: application/json' -d '{…}'

# Upload (multipart; add -F background=true to force queue ingestion)
curl -X POST $BASE/documents/upload -F tenant_id=… -F collection_id=… -F file=@doc.pdf
```

Agentic systems can call `/search` and `/chat` as tools; auth is via
`X-API-Key` / OIDC bearer tokens depending on deployment mode.
