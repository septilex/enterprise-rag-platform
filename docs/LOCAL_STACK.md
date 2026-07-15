# Running the Real Local Stack

Validated end-to-end on 2026-07-03 against **real Postgres + Redis + Qdrant +
OpenAI** (not fakes): upload → embedded, search → hits, chat → grounded answer
with citations; rows persisted in Postgres, cache keys in Redis, vectors in Qdrant.

## Option A — data services in Docker, app from source (validated path)

```bash
# 1. Infra (Postgres, Redis, Qdrant, MinIO)
docker compose -f infra/docker-compose.yml up -d

# 2. Backend (real services; cache + hybrid on)
cd backend
python -m venv venv && venv/Scripts/pip install -r requirements-dev.txt   # first time
# set OPENAI_API_KEY in backend/.env
venv/Scripts/python -m alembic upgrade head
CACHE_ENABLED=true HYBRID_ENABLED=true venv/Scripts/python -m uvicorn app.main:app --port 8000

# 2b. Ingestion worker (required for large/background uploads and source syncs)
CACHE_ENABLED=true venv/Scripts/python -m app.worker

# 3. Frontend (proxies /api -> :8000)
cd frontend
npm install            # first time
npm run dev            # http://localhost:5173
```

Create a tenant, add a collection in the UI, upload a file (📎), and ask a
question. Set `VITE_TENANT_ID` (and `VITE_API_KEY` if `API_KEYS` is set) in
`frontend/.env.local` to bind the SPA to a tenant.

## Option B — full stack in Docker

```bash
# Builds and runs backend + frontend alongside data services.
docker compose -f infra/docker-compose.yml --profile app up --build
# frontend: http://localhost:8080   backend: http://localhost:8000
```
The backend container runs `alembic upgrade head` on start. `backend/.env`
supplies `OPENAI_API_KEY`. The frontend nginx proxies `/api/*` to `rag-backend:8000`.

## Verifying it's real (not fakes)

```bash
docker exec rag_postgres psql -U rag_admin -d rag_platform -c \
  "select count(*) from chunks;"
docker exec rag_redis redis-cli --scan --pattern 'retr:*' | head
curl -s localhost:6333/collections/rag_chunks | jq '.result.points_count'
```

## Config / migration notes (Phase 4)

- `alembic check` is clean; the functional FTS index `ix_chunks_content_fts`
  is excluded from autogenerate in `alembic/env.py`.
- Runtime `DATABASE_URL` / `QDRANT_HOST` / `REDIS_URL` are overridden by env in
  compose (`postgres`, `qdrant`, `redis` service names) vs `localhost` defaults
  in `app/core/config.py` for source runs.
- Secrets: `OPENAI_API_KEY`, optional `API_KEYS`, `PRINCIPALS_JSON` via env /
  `.env` locally; via K8s Secret (`existingSecret`) in Helm.

## Kubernetes

Helm chart under `deploy/helm/rag-platform` (backend + frontend + HPA + probes +
TLS ingress + NetworkPolicy + migrate hook). **Not applied here** — this
environment has no cluster and `helm` is not installed. To validate on your side:

```bash
helm lint deploy/helm/rag-platform
helm template rag deploy/helm/rag-platform -f deploy/helm/rag-platform/values-dev.yaml
# then, against a real cluster with images pushed:
helm upgrade --install rag deploy/helm/rag-platform -f .../values-prod.yaml
```
