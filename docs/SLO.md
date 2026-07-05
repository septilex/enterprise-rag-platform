# Service Level Objectives (SLOs)

Targets from the requirements spec (§11), plus how they are measured and where
enforced. Verify with `python -m scripts.loadtest` and the Prometheus alert
rules in `deploy/monitoring/alert-rules.yaml`.

| Objective | Target | Metric / how measured |
|-----------|--------|-----------------------|
| Chat/retrieval availability | 99.9% | `rag_requests_total{status=~"5.."}` ratio; `HighErrorRate` alert |
| Retrieval latency (p95) | < 2s | `rag_retrieval_latency_seconds` histogram; `HighRetrievalLatency` alert |
| End-to-end first token (p95) | < 3s | SSE first-token (streamed); request latency histogram |
| Ingestion freshness | < 15 min | scheduler interval + worker throughput; run `completed_at - created_at` |
| Ingestion success rate | ≥ 99% | `rag_ingestion_runs_total{status}`; `IngestionFailureRate` alert |
| Multi-tenancy | 50+ isolated collections | tenant-scoped queries + cache keys (SEC-04) |
| Data durability (RPO) | ≤ 15 min | Postgres backup cadence + Qdrant snapshots (`deploy/README.md`) |
| Cost attribution freshness | < 24h | `usage_events` + `/cost/report` (MON-08) |

## Reliability guarantees in code
- **No lost jobs:** queued runs survive worker restarts; `recover_stuck_runs`
  fails orphaned runs on startup so they can be retried.
- **No duplicate runs:** `active_run_for_source` guard on manual + scheduled sync.
- **Failure recovery:** bounded retries → dead-letter queue → operator `Retry`.
- **Backpressure visibility:** `rag_ingestion_queue_depth{queue}` (incremental/
  bulk/dead) with `IngestionQueueBacklog` + `DeadLetterBacklog` alerts.

## Running the load test
```powershell
# API (and ideally a worker) running, then:
cd backend
.\venv\Scripts\python.exe -m scripts.loadtest --base http://localhost:8000 `
    --ingest 50 --queries 100 --concurrency 10
```
Prints p50/p95/p99 latency + success rate and a PASS/FAIL against the SLOs above.
Add `--api-key` when `API_KEYS` is configured.
