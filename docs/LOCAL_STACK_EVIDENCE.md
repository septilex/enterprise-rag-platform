# Local Real-Stack Proof — Evidence

**Date:** 2026-07-03
**Stack:** real Postgres 16 + Redis 7 + Qdrant + OpenAI (`text-embedding-3-small`, `gpt-4o`) + FastAPI backend + Vite/React frontend. No fakes.

## Commands run
```bash
# data services (already up)
docker compose -f infra/docker-compose.yml up -d      # rag_postgres/redis/qdrant/minio

# backend on real services
cd backend
venv/Scripts/python -m alembic current                # 0005_chat_sessions (head)
CACHE_ENABLED=true HYBRID_ENABLED=true \
  venv/Scripts/python -m uvicorn app.main:app --port 8000

# end-to-end API flow (urllib script): tenant -> collection -> upload -> search -> chat -> feedback -> session
# frontend
cd frontend && npm run dev -- --port 5173             # proxies /api -> :8000
```

## Results — PASS
| Step | Result |
|------|--------|
| health / ready | `health=200 ready=200` (real Postgres+Qdrant reachable) |
| 1 create tenant | ok (uuid returned) |
| 2 create collection | ok |
| 3 upload document (`/documents/upload`, multipart) | `status=embedded, chunks=1` |
| 4 ingest/chunk/embed | ok (OpenAI embeddings, real) |
| 5 search | `1 hit` |
| 6 chat | `grounded=True, citations=1` — "…twenty paid days per year, and remote work is allowed three days per week [1]." |
| 7 feedback | persisted (`feedback` row) |
| 8 session history | 2 messages persisted + restored |
| Frontend SPA | serves (`<title>Enterprise RAG Platform</title>`) |
| Frontend → backend proxy | search `1 hits`, chat `grounded True cites 1` |

## Persistence proof (real stores, not fakes)
```
Postgres: chunks=14, feedback=1, chat_messages=2, usage_events=3
Redis:    DBSIZE=12, embedding cache key emb:text-embedding-3-small:...  present
Qdrant:   rag_chunks points_count=16
```

## Fixes made during proof
- Multipart upload verified (curl `@file` fails in git-bash with HTTP=000; works via stdlib/requests and browser `FormData` — no backend bug).
- Alembic drift fixed earlier: functional FTS index `ix_chunks_content_fts` excluded from autogenerate in `alembic/env.py`; `alembic check` = clean.

## Env/config notes
- Source run uses `localhost` defaults in `app/core/config.py`; container run overrides `DATABASE_URL`/`QDRANT_HOST`/`REDIS_URL` to service names (see `infra/docker-compose.yml` `--profile app`).
- `OPENAI_API_KEY` from `backend/.env`. `CACHE_ENABLED`/`HYBRID_ENABLED` enabled for the run to exercise Redis + BM25.

## Remaining blockers
- **Kubernetes deploy not validated here**: no cluster context + `helm` not installed in this environment. Requires user machine/cluster (see `docs/K8S_DEPLOY.md`).
