# Production Deployment & Validation Guide

Executable runbook for deploying the Enterprise RAG Platform Helm chart to a real
managed Kubernetes cluster (EKS / GKE / AKS), verifying every workload runs, wiring
the monitoring stack, and validating real S3 + OIDC/SSO end to end.

This guide adds **no architecture, features, or code changes** — it only documents
and validates the existing chart in `deploy/helm/rag-platform`. Copy/paste commands
top to bottom.

Assumptions: `kubectl` context points at the target cluster, `helm` v3.12+, a
container registry you can push to (`$REGISTRY`), and a DNS name for the API
(`rag.example.com`). Namespace is `rag` throughout.

---

## 0. One-time cluster prerequisites

```bash
export REGISTRY=<your-registry>            # e.g. 123456789.dkr.ecr.us-east-1.amazonaws.com
export API_HOST=rag.example.com
kubectl create namespace rag --dry-run=client -o yaml | kubectl apply -f -

# Ingress controller (skip if the cluster already has one)
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace

# TLS via cert-manager + Let's Encrypt (INFRA-05)
helm repo add jetstack https://charts.jetstack.io
helm upgrade --install cert-manager jetstack/cert-manager \
  -n cert-manager --create-namespace --set crds.enabled=true
kubectl apply -f - <<'EOF'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: { name: letsencrypt-prod }
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ops@example.com
    privateKeySecretRef: { name: letsencrypt-prod }
    solvers: [{ http01: { ingress: { class: nginx } } }]
EOF
```

Cloud-specific notes (identical chart, different backing services):

| Concern            | EKS                          | GKE                        | AKS                         |
|--------------------|------------------------------|----------------------------|-----------------------------|
| Managed Postgres   | RDS Postgres (`sslmode=require`) | Cloud SQL Postgres      | Azure DB for Postgres       |
| Object storage     | S3 bucket + IRSA role         | GCS (S3-compat) / MinIO    | Blob (S3-compat) / MinIO    |
| Secret injection   | External Secrets + AWS SM     | External Secrets + GSM     | External Secrets + Key Vault|
| Ingress LB         | NLB via ingress-nginx         | GCLB / ingress-nginx       | Azure LB / ingress-nginx    |

Postgres, Qdrant, and Redis can run as managed services (recommended for prod) or
from `deploy/helm/rag-platform` sub-resources / `k8s-deps`. Set their URLs in secrets
below either way.

---

## 1. Deployment steps (step-by-step)

### 1.1 Build & push images
```bash
export TAG=$(git rev-parse --short HEAD)
docker build -t $REGISTRY/rag-api:$TAG ./backend
docker build -t $REGISTRY/rag-frontend:$TAG ./frontend
docker push $REGISTRY/rag-api:$TAG
docker push $REGISTRY/rag-frontend:$TAG
```

### 1.2 Create the runtime Secret (never bake secrets into images/ConfigMaps — SEC-01)
Use External Secrets Operator in real prod. For a first bring-up you can create the
Secret directly:
```bash
kubectl -n rag create secret generic rag-secrets \
  --from-literal=DATABASE_URL='postgresql+psycopg://user:pass@pg-host:5432/rag?sslmode=require' \
  --from-literal=REDIS_URL='redis://redis-host:6379/0' \
  --from-literal=QDRANT_URL='http://qdrant-host:6333' \
  --from-literal=OPENAI_API_KEY='sk-...' \
  --from-literal=S3_ENDPOINT_URL='https://s3.us-east-1.amazonaws.com' \
  --from-literal=S3_ACCESS_KEY_ID='AKIA...' \
  --from-literal=S3_SECRET_ACCESS_KEY='...' \
  --from-literal=OIDC_ISSUER='https://login.example.com/realms/rag' \
  --from-literal=OIDC_AUDIENCE='rag-platform' \
  --dry-run=client -o yaml | kubectl apply -f -
```
(With ESO instead, enable `externalSecret.enabled=true` in values and skip this.)

### 1.3 Install / upgrade the chart
```bash
helm upgrade --install rag deploy/helm/rag-platform \
  -n rag \
  -f deploy/helm/rag-platform/values-prod.yaml \
  --set image.repository=$REGISTRY/rag-api,image.tag=$TAG \
  --set frontend.image.repository=$REGISTRY/rag-frontend,frontend.image.tag=$TAG \
  --set ingress.hosts[0].host=$API_HOST \
  --set ingress.tls[0].hosts[0]=$API_HOST \
  --set ingress.annotations."cert-manager\.io/cluster-issuer"=letsencrypt-prod \
  --set env.AUTH_MODE=oidc \
  --wait --timeout 10m
```
The Alembic `migrate-job` runs as a Helm pre-upgrade hook, so schema is applied
before the new pods take traffic. `--wait` blocks until Deployments are Ready.

### 1.4 Subsequent zero-downtime upgrades (INFRA-04)
```bash
helm upgrade rag deploy/helm/rag-platform -n rag \
  -f deploy/helm/rag-platform/values-prod.yaml \
  --set image.tag=$NEW_TAG --wait --timeout 10m
kubectl -n rag rollout status deploy/rag-rag-platform      # RollingUpdate, maxUnavailable 0
# rollback if needed:
helm rollback rag -n rag
```

---

## 2. Cluster verification checklist

Run all; every line should succeed.

```bash
# 2.1 All workloads Ready (api, worker, scheduler, frontend)
kubectl -n rag get deploy
kubectl -n rag rollout status deploy/rag-rag-platform
kubectl -n rag rollout status deploy/rag-rag-platform-worker
kubectl -n rag rollout status deploy/rag-rag-platform-scheduler
kubectl -n rag rollout status deploy/rag-rag-platform-frontend

# 2.2 No crash-looping pods
kubectl -n rag get pods -o wide          # all Running, RESTARTS low/stable

# 2.3 Migration hook completed
kubectl -n rag get jobs                   # migrate job -> Completions 1/1

# 2.4 Service + endpoints populated
kubectl -n rag get svc,endpoints

# 2.5 Ingress has an address and TLS cert is issued
kubectl -n rag get ingress
kubectl -n rag get certificate            # READY=True
kubectl -n rag describe certificate | grep -i 'issued\|ready'

# 2.6 HTTP liveness through the real ingress + TLS
curl -fsS https://$API_HOST/health        # {"status":"ok"}
curl -fsS https://$API_HOST/ready         # dependencies (db/redis/qdrant) all "ok"
curl -fsSI https://$API_HOST/ | grep -i strict-transport-security   # HSTS present

# 2.7 Worker is alive and draining the queue (Redis heartbeat, not /metrics)
curl -fsS https://$API_HOST/admin/system/status -H "X-API-Key: $ADMIN_KEY" | jq .worker

# 2.8 End-to-end smoke (upload -> embed -> search -> grounded chat + citations)
kubectl -n rag exec deploy/rag-rag-platform -- \
  python -m scripts.smoke_test --base http://localhost:8000
# expect: SMOKE TEST PASSED

# 2.9 SLO gate (optional, load-based)
kubectl -n rag exec deploy/rag-rag-platform -- \
  python -m scripts.slo_verify --base http://localhost:8000
```

---

## 3. Monitoring setup verification (MON-02/06)

Install the operator stack, then apply the three artifacts in `deploy/monitoring/`.

```bash
# 3.1 kube-prometheus-stack (Prometheus + Alertmanager + Grafana)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm upgrade --install kube-prometheus-stack \
  prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace

# 3.2 Wire our scrape target + SLO alerts (release label matches the operator)
kubectl apply -f deploy/monitoring/servicemonitor.yaml
kubectl apply -f deploy/monitoring/prometheusrule.yaml

# 3.3 Confirm Prometheus discovered the API target
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
#  -> http://localhost:9090/targets  : serviceMonitor/rag/rag-platform is UP
#  -> run query: rag_requests_total  : returns series
#  -> http://localhost:9090/alerts   : the 8 rag-platform-slo rules are loaded

# 3.4 Import the dashboard into Grafana
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80 &
#  Grafana -> Dashboards -> Import -> upload deploy/monitoring/grafana-dashboard.json
#  Panels populate: request rate, 5xx %, p95 latency, retrieval p50/95/99,
#  queries/tenant, ingestion runs, queue depth, cache hit ratio.
```

Health visibility confirmed when:
- **API**: `up{job=~".*rag-platform.*"} == 1` and request/latency panels move under load.
- **Worker**: `rag_ingestion_queue_depth{queue="incremental"}` drains toward 0 after
  ingestion; `queue="dead"` stays 0. (Worker liveness itself is exposed via
  `/admin/system/status`; it runs no HTTP `/metrics` server by design.)
- **Alerts**: force one (e.g. stop the worker, push jobs) → `IngestionQueueBacklog`
  fires in Alertmanager.

---

## 4. Real S3 + SSO validation (not mock)

### 4.1 Real S3 ingestion
Precondition: `S3_ENDPOINT_URL / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY` are in the
Secret (§1.2), and the IAM principal can `s3:GetObject`/`s3:ListBucket` on the bucket.

```bash
# Put a real document in the bucket
aws s3 cp ./sample.pdf s3://my-rag-bucket/docs/sample.pdf

# Register an S3 source (source_type=s3, NOT s3_mock) and trigger a sync
curl -fsS https://$API_HOST/sources -H "X-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' -d '{
    "tenant_id":"'$TID'","collection_id":"'$CID'",
    "source_type":"s3","config":{"bucket":"my-rag-bucket","prefix":"docs/"}}'

curl -fsS https://$API_HOST/sources/$SID/sync -X POST -H "X-API-Key: $ADMIN_KEY"

# Verify the run succeeded and documents landed
curl -fsS "https://$API_HOST/ingestion/runs?tenant_id=$TID&collection_id=$CID" \
  -H "X-API-Key: $ADMIN_KEY" | jq '.[0]'      # status "succeeded", documents_ingested > 0
curl -fsS https://$API_HOST/search -H "X-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"'$TID'","collection_id":"'$CID'","query":"<term from sample.pdf>"}' \
  | jq '.total'                                # > 0
```
Fail signals to check: `AccessDenied` (IAM/IRSA), `NoSuchBucket`/endpoint typo, or a
run stuck `running` (worker not draining — see §3). Transient 429/5xx are auto-retried
by the connector.

### 4.2 Real OIDC / SSO login flow
Precondition: chart installed with `env.AUTH_MODE=oidc`, and `OIDC_ISSUER`/
`OIDC_AUDIENCE` set to your IdP (Keycloak/Okta/Entra ID/Auth0). The API verifies RS256
via the issuer's JWKS.

```bash
# 4.2.1 API rejects unauthenticated calls under oidc mode
curl -s -o /dev/null -w '%{http_code}\n' https://$API_HOST/tenants   # 401

# 4.2.2 Get a real token from the IdP (client-credentials shown; use auth-code for UI)
TOKEN=$(curl -fsS https://login.example.com/realms/rag/protocol/openid-connect/token \
  -d grant_type=client_credentials -d client_id=rag-platform -d client_secret=$SECRET \
  | jq -r .access_token)

# 4.2.3 Authenticated call succeeds and identity is resolved
curl -fsS https://$API_HOST/tenants -H "Authorization: Bearer $TOKEN" | jq .
curl -fsS https://$API_HOST/me      -H "Authorization: Bearer $TOKEN" | jq .  # email + roles

# 4.2.4 RBAC enforced: viewer token blocked from a write (expect 403)
curl -s -o /dev/null -w '%{http_code}\n' https://$API_HOST/tenants \
  -H "Authorization: Bearer $VIEWER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"x"}'                                                            # 403
```
UI flow: open `https://$API_HOST`, click **Sign in** → redirected to the IdP →
after login you are returned with a session and the workspace loads. Confirm the JWT
`aud`/`iss` match `OIDC_AUDIENCE`/`OIDC_ISSUER`; a mismatch surfaces as 401 with an
`invalid_token` log line (query it in Grafana/Loki via `request_id`).

---

## 5. Final status

**READY FOR REAL DEPLOYMENT: YES**

The chart deploys API + worker + scheduler + frontend with rolling upgrades, DB
migration hook, ingress + cert-manager TLS, HPA (CPU + queue-depth), per-tenant
quotas, backups, and secret injection via ESO. Monitoring is complete and
apply-ready (ServiceMonitor + PrometheusRule + Grafana dashboard, all referencing
metrics the app actually emits). Real S3 and OIDC/SSO validation flows are covered by
copy-paste commands above and the in-cluster `scripts.smoke_test` / `scripts.slo_verify`
gates.

Two operational preconditions the operator must supply (not code gaps):
1. Real managed backing services (Postgres/Qdrant/Redis/S3) and IdP reachable from the
   cluster, with their URLs/creds in `rag-secrets` (or ESO).
2. For the `retrieval p95 < 2s` SLO, use an embedding endpoint co-located with the
   cluster (self-hosted OpenAI-compatible or same-region), since p95 is dominated by
   embedding round-trip latency.
