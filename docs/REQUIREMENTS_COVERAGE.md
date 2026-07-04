# Requirements Coverage — RAG Platform v1.0

Status of every PDF requirement against the backend. **78 tests green.**
Feature flags default OFF to preserve baseline behavior; production values files
turn them on. Flags: `HYBRID_ENABLED`, `RERANK_ENABLED`, `CACHE_ENABLED`,
`SEMANTIC_CACHE_ENABLED`, `QUERY_TRANSFORM`, `MULTI_HOP_ENABLED`, `API_KEYS`,
`PRINCIPALS_JSON`.

Legend: ✅ done+tested · 🟡 partial · ⬜ not started (infra/frontend)

## Infrastructure & Deployment
| ID | Status | Evidence |
|----|--------|----------|
| INFRA-01 | ✅ | `deploy/helm/rag-platform` chart + values-dev/staging/prod |
| INFRA-02 | ✅ | `templates/hpa.yaml` (CPU+memory HPA) |
| INFRA-03 | 🟡 | volumes + backup/restore runbook (`deploy/README.md`); automation TODO |
| INFRA-04 | ✅ | `strategy` RollingUpdate maxUnavailable:0 |
| INFRA-05 | ✅ | `/health` liveness + `/ready` readiness, `test_probes.py` |
| INFRA-06 | ✅ | `existingSecret` (secretRef), `.env` gitignored |
| INFRA-07 | ✅ | Alembic pre-upgrade hook Job (`migrate-job.yaml`) |
| INFRA-08 | ✅ | resources on container in `values.yaml` |
| INFRA-09 | ⬜ | GPU node pools (cluster-level) |
| INFRA-10 | 🟡 | ING-10 reindex + DR via backups (procedure) |

## Dynamic Data Ingestion
| ID | Status | Evidence |
|----|--------|----------|
| ING-01 | ✅ | `services/connectors.py` registry + text/filesystem, `test_connectors.py` |
| ING-02 | ✅ | delta re-index in `ingest_text_document`, `test_ingestion.py` |
| ING-03 | ✅ | `POST /ingest/webhook`, `test_webhook_ingest.py` |
| ING-04 | ✅ | content-hash idempotency, `test_ingestion.py` |
| ING-05 | ✅ | per-collection chunking, `test_chunking.py` |
| ING-06 | ✅ | metadata tagging + payload, `test_metadata_filter.py` |
| ING-07 | ✅ | quarantine + `GET /documents?status=quarantined`, `test_quarantine.py` |
| ING-08 | ✅ | delete propagation, `test_ingestion.py` |
| ING-09 | ✅ | Redis priority queues + `/ingest/batch`, `test_jobs.py` |
| ING-10 | ✅ | `services/reindex.py`, `test_reindex.py` |

## Retrieval
| ID | Status | Evidence |
|----|--------|----------|
| RET-01 | ✅ | hybrid dense+BM25+RRF, `test_hybrid.py` |
| RET-02 | ✅ | `VectorStore` ABC (Qdrant + in-memory) |
| RET-03 | ✅ | `CrossEncoderReranker` + `/search/debug` |
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
| MON-02 | ✅ | `/metrics`, `test_observability.py` |
| MON-03 | ✅ | `/eval/scorecard`, `test_evaluation.py` |
| MON-04 | ✅ | structured query log + PII redaction (`redact_pii`), `test_pii_and_tracing_export.py` |
| MON-05 | ✅ | drift detection, `test_drift.py` |
| MON-06 | ✅ | `deploy/monitoring/alert-rules.yaml` |
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
| SEC-01 | ✅ | API-key auth, `test_auth.py` |
| SEC-02 | ✅ | RBAC principals, `test_rbac.py` |
| SEC-03 | 🟡 | TLS ingress + NetworkPolicy templates (`ingress.yaml`, `networkpolicy.yaml`); at-rest encryption + live TLS unverified |
| SEC-04 | ✅ | tenant isolation (RET-05/CACHE-07 tests) |
| SEC-05 | ✅ | audit log, `test_audit.py` |
| SEC-06 | ✅ | erasure, `test_erasure.py` |

## Not started (out of backend scope)
Web chat frontend (UI-07/08), MON-09 external tool, INFRA-09 GPU pools,
ING-09 distributed worker queue, automated backup/DR jobs.
