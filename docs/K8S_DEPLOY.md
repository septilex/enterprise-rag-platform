# Kubernetes Deployment Proof — Runbook

**Status from this environment:** NOT cluster-validated. No kube-context is
reachable here and `helm` is not installed, so `helm lint` / `helm template` /
`kubectl apply` were not run. YAML *syntax* of `deploy/k8s-deps.yaml` and all
`values*.yaml` is validated; Go-templated chart templates require `helm` on your
side. Run the steps below on your machine/cluster.

## Chart inventory (`deploy/helm/rag-platform`)
- `deployment.yaml` — backend, 2 probes (liveness `/health`, readiness `/ready`), resources ✓
- `frontend.yaml` — frontend Deployment + Service + stable `rag-backend` Service, resources ✓
- `service.yaml`, `hpa.yaml` (CPU+mem), `ingress.yaml` (TLS), `networkpolicy.yaml`
- `migrate-job.yaml` — `alembic upgrade head` pre-install/upgrade hook
- `secret.yaml` — optional dev secret (`createSecret`)
- Data services are NOT in the chart → use `deploy/k8s-deps.yaml` (dev) or managed DBs.

## Prereqs
```bash
kubectl config current-context && kubectl get nodes    # cluster reachable
helm version                                            # helm installed
kubectl create namespace rag
```

## 1. Build images
```bash
# Lightweight CPU image (no torch). RERANK_ENABLED must stay false.
docker build -t rag-platform:0.1.0 backend
docker build -t rag-platform-frontend:0.1.0 frontend
# Reranking image (heavy, only if RERANK_ENABLED=true):
# docker build --build-arg INSTALL_RERANK=true -t rag-platform:0.1.0-rerank backend
```

## 2. Make images visible to the cluster (pick one)
```bash
# kind
kind load docker-image rag-platform:0.1.0 rag-platform-frontend:0.1.0
# minikube
minikube image load rag-platform:0.1.0 && minikube image load rag-platform-frontend:0.1.0
# remote registry
docker tag rag-platform:0.1.0 <REG>/rag-platform:0.1.0 && docker push <REG>/rag-platform:0.1.0
docker tag rag-platform-frontend:0.1.0 <REG>/rag-platform-frontend:0.1.0 && docker push <REG>/rag-platform-frontend:0.1.0
```

## 3. Dependencies (dev) + secrets
```bash
kubectl -n rag apply -f deploy/k8s-deps.yaml          # postgres/redis/qdrant (ephemeral, DEV only)
kubectl -n rag rollout status deploy/postgres deploy/redis deploy/qdrant

kubectl -n rag create secret generic rag-platform-secrets \
  --from-literal=OPENAI_API_KEY=sk-... \
  --from-literal=API_KEYS=prodkey1 \
  --from-literal=PRINCIPALS_JSON='{}'
# TLS (skip if cert-manager issues it via the ingress annotation):
kubectl -n rag create secret tls rag-platform-tls --cert=tls.crt --key=tls.key
```

## 4. Values to set (`values-prod.yaml` / `--set`)
- `ingress.host` = your host · `ingress.className` = your controller
- delete `cert-manager.io/cluster-issuer` annotation if not using cert-manager (rely on the TLS secret)
- `env.DATABASE_URL/QDRANT_HOST/REDIS_URL` already point at `postgres/qdrant/redis` (match deps)
- local images: add `--set image.pullPolicy=IfNotPresent`
- registry images: `--set image.repository=<REG>/rag-platform --set frontend.image.repository=<REG>/rag-platform-frontend`

## 5. Deploy
```bash
helm lint deploy/helm/rag-platform
helm template rag deploy/helm/rag-platform -n rag -f deploy/helm/rag-platform/values-prod.yaml   # render check
helm upgrade --install rag deploy/helm/rag-platform -n rag \
  -f deploy/helm/rag-platform/values-prod.yaml \
  --set image.pullPolicy=IfNotPresent \
  --wait --timeout 10m
```

## 6. Verify
```bash
kubectl -n rag get pods,svc,ingress,hpa
kubectl -n rag logs job/rag-rag-platform-migrate            # expect: upgrade ... -> 0005_chat_sessions
kubectl -n rag rollout status deploy/rag-rag-platform
kubectl -n rag rollout status deploy/rag-rag-platform-frontend
kubectl -n rag exec deploy/rag-rag-platform -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/ready').status)"   # 200
kubectl -n rag describe ingress rag-rag-platform | grep -E "Host|TLS|Address"
```
(Release `rag` + chart `rag-platform` → resource prefix `rag-rag-platform`.)

## 7. Smoke test after deploy
```bash
# no ingress? port-forward:
kubectl -n rag port-forward svc/rag-backend 8000:8000 &
B=http://localhost:8000; K='-H X-API-Key:prodkey1'
TID=$(curl -s $K -X POST $B/tenants -H 'content-type: application/json' -d '{"name":"k8s-smoke"}' | jq -r .id)
CID=$(curl -s $K -X POST $B/collections -H 'content-type: application/json' -d "{\"tenant_id\":\"$TID\",\"name\":\"hb\"}" | jq -r .id)
printf 'Remote work is allowed three days per week.' > f.txt
curl -s $K -X POST $B/documents/upload -F tenant_id=$TID -F collection_id=$CID -F "file=@f.txt;type=text/plain"
curl -s $K -X POST $B/search -H 'content-type: application/json' -d "{\"tenant_id\":\"$TID\",\"collection_id\":\"$CID\",\"query\":\"remote work\",\"top_k\":3}"
curl -s $K -X POST $B/chat   -H 'content-type: application/json' -d "{\"tenant_id\":\"$TID\",\"collection_id\":\"$CID\",\"query\":\"how many remote days?\"}"
# UI: kubectl -n rag port-forward svc/rag-rag-platform-frontend 8080:80  -> http://localhost:8080
```

## Report back
Paste output of step 5 (`helm lint` / `upgrade`) and step 6. I'll fix any template/wiring failure.
