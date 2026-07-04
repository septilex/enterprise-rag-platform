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
