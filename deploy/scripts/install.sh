#!/usr/bin/env bash
# Item 1: Helm install validation — build/load images, install the chart, and
# verify every workload rolls out. Additive; does not change the chart.
set -euo pipefail

NS="${NS:-rag}"
RELEASE="${RELEASE:-rag}"
CHART="${CHART:-deploy/helm/rag-platform}"
VALUES="${VALUES:-$CHART/values-dev.yaml}"
TAG="${TAG:-0.1.0}"
LOADER="${LOADER:-}"   # kind | minikube | "" (registry/pushed images)

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

log "Preflight"
command -v helm  >/dev/null || { echo "helm not found"; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
kubectl cluster-info >/dev/null 2>&1 || { echo "no reachable cluster (kubectl context)"; exit 1; }

log "helm lint"
helm lint "$CHART"

log "Build images"
docker build -t "rag-platform:$TAG" backend
docker build -t "rag-platform-frontend:$TAG" frontend

case "$LOADER" in
  kind)     log "kind load"; kind load docker-image "rag-platform:$TAG" "rag-platform-frontend:$TAG" ;;
  minikube) log "minikube load"; minikube image load "rag-platform:$TAG"; minikube image load "rag-platform-frontend:$TAG" ;;
  *)        log "Using images as-is (ensure pushed/available to the cluster)" ;;
esac

log "Namespace + dev dependencies (Postgres/Redis/Qdrant)"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" apply -f deploy/k8s-deps.yaml
kubectl -n "$NS" rollout status deploy/postgres --timeout=120s
kubectl -n "$NS" rollout status deploy/redis    --timeout=120s
kubectl -n "$NS" rollout status deploy/qdrant   --timeout=120s

log "Dev secret (override in prod with ExternalSecrets)"
kubectl -n "$NS" create secret generic rag-platform-secrets \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-sk-REPLACE}" \
  --from-literal=API_KEYS="${API_KEYS:-}" \
  --from-literal=PRINCIPALS_JSON="${PRINCIPALS_JSON:-{}}" \
  --dry-run=client -o yaml | kubectl apply -f -

log "helm upgrade --install"
helm upgrade --install "$RELEASE" "$CHART" -n "$NS" -f "$VALUES" \
  --set image.pullPolicy=IfNotPresent \
  --set image.tag="$TAG" --set frontend.image.tag="$TAG" \
  --set ingress.enabled=false --set externalSecret.enabled=false \
  --wait --timeout 10m

log "Verify rollouts"
for d in "$RELEASE-rag-platform" "$RELEASE-rag-platform-worker" \
         "$RELEASE-rag-platform-scheduler" "$RELEASE-rag-platform-frontend"; do
  kubectl -n "$NS" rollout status "deploy/$d" --timeout=180s
done

log "Migrate hook Job result"
kubectl -n "$NS" get jobs -l app.kubernetes.io/name=rag-platform || true

log "Readiness probe"
kubectl -n "$NS" exec "deploy/$RELEASE-rag-platform" -- \
  python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/ready').status==200 else 1)"

log "SUCCESS — all workloads installed and ready in namespace '$NS'"
echo "Port-forward:  kubectl -n $NS port-forward svc/rag-backend 8000:8000"
