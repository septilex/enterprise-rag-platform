# RAG Platform — Web Chat Frontend

React + Vite + TypeScript SPA for the RAG Platform backend. Covers UI-01
(streaming), UI-02 (clickable citations), UI-03 (multi-session history), UI-04
(collection scoping), UI-05 (feedback), UI-08 (responsive), UI-09 (no-answer).

## Dev
```bash
cp .env.example .env.local   # set VITE_TENANT_ID, VITE_API_KEY if auth on
npm install
npm run dev                  # http://localhost:5173 (proxies /api -> :8000)
```
Backend must be running (`uvicorn app.main:app --port 8000`).

## Build / container
```bash
npm run build                # -> dist/
docker build -t rag-platform-frontend:0.1.0 .
```
The container serves the SPA via nginx and proxies `/api/*` to the in-cluster
`rag-backend:8000` service. TLS terminates at the Helm ingress (`deploy/helm`).

## Not implemented
- UI-07 (session-scoped file upload) — requires a backend upload endpoint.
