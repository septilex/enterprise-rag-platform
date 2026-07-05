# Deployment

## Helm (INFRA-01)
```bash
docker build -t rag-platform:0.1.0 ../backend
helm install rag ./helm/rag-platform -f ./helm/rag-platform/values-dev.yaml
# staging / prod:
helm upgrade --install rag ./helm/rag-platform -f ./helm/rag-platform/values-prod.yaml
```
The pre-upgrade hook Job runs `alembic upgrade head` before each rollout (INFRA-07).
Rollouts are zero-downtime (`maxUnavailable: 0`, readiness-gated) (INFRA-04/05).

## Secrets (INFRA-06)
Provision a Secret named per `existingSecret` with `OPENAI_API_KEY`, `API_KEYS`,
`PRINCIPALS_JSON` via Vault or External Secrets Operator. Never bake secrets
into images/ConfigMaps. `createSecret: true` exists for local dev only.

## Observability
- `/metrics` scraped via pod annotations (MON-02); load `monitoring/alert-rules.yaml`
  into Prometheus (MON-06).
- OpenTelemetry traces export when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (MON-01).

## Ingestion workers (ING-09)
Run one or more worker processes draining the Redis queues (incremental before
bulk):
```python
from app.db.base import SessionLocal
from app.services.jobs import RedisJobQueue, process_next
# loop: process_next(SessionLocal(), queue, embedder, vector_store, cache)
```

## Backup & restore (INFRA-03 / data durability, RPO ≤ 15m)
- **Postgres**: scheduled `pg_dump`/WAL archiving to S3-compatible storage.
  Restore: `pg_restore` then `alembic upgrade head`.
- **Qdrant**: snapshot API to object storage; restore by loading the snapshot.
  After restore, `ING-10` reindex can rebuild vectors from Postgres chunks if a
  vector snapshot is unavailable.
- **Object storage (MinIO/S3)**: bucket replication/versioning.
Disaster recovery drill: restore Postgres + Qdrant snapshots into a fresh
cluster, `helm install`, verify `/ready` (INFRA-10).
```

## Full workload set (this chart)
`helm install` now renders: **API** Deployment (probes, securityContext, HPA),
**worker** Deployment (ING-09 queue drain), **scheduler** Deployment (ING-03
cron sync), **frontend** Deployment + nginx, backend alias Service, Ingress
(TLS), NetworkPolicy, Alembic migrate hook Job, and optionally an ExternalSecret
(ESO). Toggle components via `worker.enabled` / `scheduler.enabled` /
`frontend.enabled` / `ingress.enabled` / `externalSecret.enabled`.

## Zero-downtime deploys (INFRA-04)
- API/frontend use `RollingUpdate` with `maxUnavailable: 0`, `maxSurge: 1`:
  new pods must pass the readiness probe (`/ready`) before old pods drain, so no
  request hits a not-ready pod.
- Schema changes run in the **pre-upgrade hook Job** (`alembic upgrade head`)
  before new pods roll — migrations are additive/backward-compatible so the old
  version keeps serving during the migration.
- Worker/scheduler are safe to roll anytime: queued runs survive restarts and
  `recover_stuck_runs` fails-then-allows-retry of anything orphaned mid-flight;
  the idempotent active-run guard prevents duplicate work.

## Security posture (SEC-03)
- Non-root, read-only-rootfs, all-caps-dropped containers (`podSecurityContext` /
  `containerSecurityContext`).
- TLS terminates at the Ingress; set `DATABASE_URL=...?sslmode=require` for
  in-transit DB encryption; use encrypted PVs / SSE buckets for at-rest.
- Secrets via `existingSecret` or `externalSecret` (External Secrets Operator) —
  never baked into images or values.
- SSO: set `AUTH_MODE=oidc` + `OIDC_ISSUER/OIDC_AUDIENCE/OIDC_JWKS_URL`; the API
  validates bearer JWTs against the provider JWKS.

## Load / SLO validation
See `docs/SLO.md`; run `python -m scripts.loadtest` against a deployed endpoint.

## Autoscaling on queue depth (INFRA-02)
The worker HPA (`worker.autoscaling.enabled`) scales on CPU **and** the external
metric `rag_ingestion_queue_depth{queue="incremental"}`. Expose it to the HPA API
with prometheus-adapter:
```yaml
# prometheus-adapter rules (excerpt)
rules:
  external:
    - seriesQuery: 'rag_ingestion_queue_depth{queue="incremental"}'
      resources: { namespaced: false }
      name: { as: "rag_ingestion_queue_depth" }
      metricsQuery: 'max(rag_ingestion_queue_depth{queue="incremental"})'
```
Without prometheus-adapter the worker still scales on CPU; the external metric is
simply ignored by the HPA controller.

## Per-tenant resource quota (INFRA-08)
Deploy the chart per tenant namespace and set `resourceQuota.enabled=true` with
`resourceQuota.hard` caps; the API server then rejects workloads that would
breach the tenant's namespace quota.
