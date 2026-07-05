# Requirements Coverage — RAG Platform v1.0

Status of every PDF requirement against the backend. **137 tests green.**
Feature flags default OFF to preserve baseline behavior; production values files
turn them on. Flags: `HYBRID_ENABLED`, `RERANK_ENABLED`, `CACHE_ENABLED`,
`SEMANTIC_CACHE_ENABLED`, `QUERY_TRANSFORM`, `MULTI_HOP_ENABLED`, `API_KEYS`,
`PRINCIPALS_JSON`.

Legend: ✅ done+tested · 🟡 partial · ⬜ not started (infra/frontend)

## Infrastructure & Deployment
| ID | Status | Evidence |
|----|--------|----------|
| INFRA-01 | ✅ | `deploy/helm/rag-platform` chart + values-dev/staging/prod |
| INFRA-02 | ✅ | API HPA (CPU+memory) + **worker HPA on queue depth** (`worker-hpa.yaml`, external `rag_ingestion_queue_depth`) |
| INFRA-03 | 🟡 | volumes + backup/restore runbook (`deploy/README.md`); automation TODO |
| INFRA-04 | ✅ | `strategy` RollingUpdate maxUnavailable:0 |
| INFRA-05 | ✅ | `/health` liveness + `/ready` readiness, `test_probes.py` |
| INFRA-06 | ✅ | `existingSecret` (secretRef), `.env` gitignored |
| INFRA-07 | ✅ | Alembic pre-upgrade hook Job (`migrate-job.yaml`) |
| INFRA-08 | ✅ | per-container requests/limits + **per-tenant `ResourceQuota`** (`resourcequota.yaml`) |
| INFRA-09 | ⬜ | GPU node pools (cluster-level) |
| INFRA-10 | 🟡 | ING-10 reindex + DR via backups (procedure) |

## Dynamic Data Ingestion
| ID | Status | Evidence |
|----|--------|----------|
| ING-01 | ✅ | `Source` model + `services/ingestion_runs.py` orchestrator (manual_upload/api_text/webhook/connector = source types) + `services/connectors.py` registry; `/sources`; `test_ingestion_platform.py` |
| ING-02 | ✅ | delta re-index in `ingest_text_document`, `test_ingestion.py` |
| ING-03 | ✅ | webhook + on-demand sync + **cron scheduler** (`app/scheduler.py`, due-detection, idempotent enqueue, Helm/compose); `test_production_hardening.py` |
| ING-04 | ✅ | content-hash idempotency, `test_ingestion.py` |
| ING-05 | ✅ | per-collection chunking, `test_chunking.py` |
| ING-06 | ✅ | metadata tagging + payload, `test_metadata_filter.py` |
| ING-07 | ✅ | quarantine + `IngestionRun` status=failed/partial + `GET /ingestion/runs` + `GET /documents?status=quarantined`, `test_ingestion_platform.py` |
| ING-08 | ✅ | delete propagation + **connector deletion delta** (`documents_deleted`), `test_ops_reliability.py` |
| ING-09 | ✅ | Redis queue + **background worker** (`app/worker.py`, Helm `worker.yaml`) draining sync jobs, queued→running→done + retry; `test_connector_platform.py` |
| ING-10 | ✅ | `services/reindex.py`, `test_reindex.py` |

## Retrieval
| ID | Status | Evidence |
|----|--------|----------|
| RET-01 | ✅ | hybrid dense+BM25+RRF, `test_hybrid.py` |
| RET-02 | ✅ | `VectorStore` ABC (Qdrant + in-memory) |
| RET-03 | ✅ | modular rerank stage: `HeuristicReranker` (no-torch, default) + `CrossEncoderReranker`, strategy-selectable; `/search/debug`; `test_reranker.py` |
| RET-04 | ✅ | metadata/ACL filter, `test_metadata_filter.py` |
| RET-05 | ✅ | tenant+collection scoping |
| RET-06 | ✅ | dedup + token budget + cap, `test_generation.py` |
| RET-07 | ✅ | structured multi-citations, `test_generation.py` |
| RET-08 | ✅ | rewrite/HyDE, `test_query_transform.py` |
| RET-09 | ✅ | multi-hop, `test_multihop.py` |
| RET-10 | ✅ | `Embedder` ABC (+ `CachedEmbedder`) |

## Caching
| ID | Status | Evidence |
|----|--------|----------|
| CACHE-01 | ✅ | `CachedEmbedder`, `test_cache.py` |
| CACHE-02 | ✅ | semantic cache, `test_semantic_cache.py` |
| CACHE-03 | ✅ | retrieval cache, `test_cache.py` |
| CACHE-04 | ✅ | TTL + invalidation on ingest/delete |
| CACHE-05 | ✅ | Redis shared store |
| CACHE-06 | ✅ | hit/miss/saved metrics, `test_cache_metrics.py` |
| CACHE-07 | ✅ | tenant-namespaced keys |
| CACHE-08 | ✅ | `no_cache` bypass |

## Monitoring & Observability
| ID | Status | Evidence |
|----|--------|----------|
| MON-01 | ✅ | OTel spans + OTLP/console exporter wiring (`tracing.py`); live Jaeger view needs a collector |
| MON-02 | ✅ | `/metrics` + `/admin/system/status` (worker heartbeat, queue/DLQ depth, ingestion success rate, source health) + **Operations dashboard UI**; `test_platform_metrics.py`, `test_ops_reliability.py` |
| MON-03 | ✅ | `/eval/scorecard`, `test_evaluation.py` |
| MON-04 | ✅ | structured query log + PII redaction + **JSON log formatter** (`LOG_FORMAT=json`) + request-id correlation; `test_pii_and_tracing_export.py`, `test_production_hardening.py` |
| MON-05 | ✅ | drift detection, `test_drift.py` |
| MON-06 | ✅ | `alert-rules.yaml` (latency, error rate, ingestion-failure-rate, DLQ/queue backlog, drift) |
| MON-07 | ✅ | `/feedback`, `test_feedback.py` |
| MON-08 | ✅ | `/cost/report`, `test_cost.py` |
| MON-09 | ⬜ | external LLM-observability tool integration |

## Chat UI (API surface)
| ID | Status | Evidence |
|----|--------|----------|
| UI-01 | ✅ | `/chat/stream` SSE + `frontend/` React SPA streaming |
| UI-02 | ✅ | clickable citations `frontend/src/components/Citations.tsx` |
| UI-03 | ✅ | sessions+messages API + SPA sidebar, `test_sessions.py` |
| UI-04 | ✅ | collection selector in SPA + per-request scoping |
| UI-05 | ✅ | feedback endpoint + SPA thumbs up/down |
| UI-06 | ✅ | documented REST/SSE API |
| UI-07 | ✅ | `POST /documents/upload` + SPA 📎 uploader, `test_upload.py` |
| UI-08 | ✅ | responsive SPA (`styles.css` @media), Vite build passes |
| UI-09 | ✅ | no-answer guard + SPA indicator |

## Security & Governance
| ID | Status | Evidence |
|----|--------|----------|
| SEC-01 | ✅ | API-key + DB identity (`dev`) + **OIDC/SSO** bearer JWT (`AUTH_MODE=oidc`, JWKS/RS256 + HS256 dev verifier, auto-provision); `test_production_hardening.py` |
| SEC-02 | ✅ | RBAC via `users`/`memberships` (admin/editor/viewer) enforced in service logic on collection/upload/delete/admin; `test_identity_rbac.py`, `test_rbac.py` |
| SEC-03 | 🟡 | TLS ingress + NetworkPolicy + **pod/container securityContext** (non-root, RO-rootfs, drop caps) + ExternalSecrets template + DB-TLS values note; at-rest encryption is storage-class level (unverified live) |
| SEC-04 | ✅ | tenant isolation (RET-05/CACHE-07 tests) |
| SEC-05 | ✅ | persistent immutable `audit_log` table + `/admin/audit` + **Audit log UI**; append-only; `test_identity_rbac.py`, `test_audit.py` |
| SEC-06 | ✅ | erasure, `test_erasure.py` |

## Remaining gaps (infra / environment level only)
- **Live-cluster deploy proof** — Helm chart complete (API + worker + scheduler +
  frontend + ingress/TLS + NetworkPolicy + securityContext + ExternalSecrets +
  migrate hook + HPA) but not applied to a real cluster here (`helm`/cluster
  unavailable). See `docs/K8S_DEPLOY.md`.
- **SEC-03 at-rest encryption** — enforced at the storage-class / managed-DB
  level (encrypted PVs, `sslmode=require`); config + docs in place, not verified live.
- **INFRA-09** GPU node pools; **MON-09** external LLM-observability tool (Langfuse/Opik).
- **Real cloud connectors** ship as isolated adapters (`s3` boto3, `confluence`
  httpx) — need live creds to exercise; `s3_mock`/`filesystem` fully tested.
- **Collection/document-level RBAC** (tenant-level roles implemented) and
  **automated backup/DR jobs** (runbook exists).
- **NFR/SLO** — `scripts/loadtest.py` + `docs/SLO.md`; p95<2s retrieval needs a
  co-located/self-hosted embedding endpoint (external-provider RTT dominates locally).
