#!/usr/bin/env bash
# Eval-gated canary drill on kind:
#  1. install Argo Rollouts and the chart (rollouts on), wait Healthy, smoke test;
#  2. ship a deliberately broken build (FIELD_FAULT=empty_retrieval): the canary's
#     golden-set self-check fails, the analysis fails, the rollout aborts;
#  3. check the stable build still serves with a passing score;
#  4. ship a good change: the analysis passes and it is promoted.
set -euo pipefail
NS=copilot
REL=demo
ROLLOUTS_VERSION=v1.7.2
CHART=deploy/helm/field-copilot
COMMON=(--set rollouts.enabled=true --set api.replicas=2
        --set image.api.repository=field-copilot-api --set image.api.tag=ci --set image.api.pullPolicy=Never
        --set image.web.repository=field-copilot-web --set image.web.tag=ci --set image.web.pullPolicy=Never)

kubectl create namespace argo-rollouts --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argo-rollouts -f "https://github.com/argoproj/argo-rollouts/releases/download/${ROLLOUTS_VERSION}/install.yaml"
kubectl -n argo-rollouts rollout status deploy/argo-rollouts --timeout=180s

wait_phase() {
  local want=$1 phase=""
  for _ in $(seq 1 100); do
    phase=$(kubectl -n "$NS" get rollout "$REL-api" -o jsonpath='{.status.phase}' 2>/dev/null || true)
    [ "$phase" = "$want" ] && { echo "rollout is $phase"; return 0; }
    sleep 4
  done
  echo "rollout phase is '$phase', wanted '$want'"
  kubectl -n "$NS" get rollout "$REL-api" -o yaml | tail -40
  kubectl -n "$NS" get analysisrun -o yaml | tail -60
  kubectl -n "$NS" get pods
  return 1
}

# Port-forward to the stable Service (Argo Rollouts pins its selector to the stable pods).
smoke() {
  kubectl -n "$NS" port-forward "svc/$REL-api" 8020:8020 >/tmp/pf.log 2>&1 &
  local pf=$! rc=0
  sleep 4
  python deploy/scripts/smoke.py http://127.0.0.1:8020 demo-password --min-score "$1" || rc=$?
  kill $pf 2>/dev/null || true
  wait $pf 2>/dev/null || true
  return $rc
}

stable_hash() { kubectl -n "$NS" get rollout "$REL-api" -o jsonpath='{.status.stableRS}'; }

# Wait until a new revision has been promoted: stable hash changed and phase Healthy.
wait_promoted() {
  local old=$1
  for _ in $(seq 1 100); do
    local now phase
    now=$(stable_hash); phase=$(kubectl -n "$NS" get rollout "$REL-api" -o jsonpath='{.status.phase}')
    if [ "$now" != "$old" ] && [ "$phase" = "Healthy" ]; then echo "promoted $old -> $now"; return 0; fi
    sleep 4
  done
  echo "not promoted (stable still $old)"; kubectl -n "$NS" get analysisrun; return 1
}

echo "== 1. install"
helm upgrade --install "$REL" "$CHART" -n "$NS" --create-namespace "${COMMON[@]}" --wait --timeout 8m
wait_phase Healthy
smoke 0.75

echo "== 2. broken build: retrieval returns nothing"
GOOD=$(stable_hash)
helm upgrade "$REL" "$CHART" -n "$NS" "${COMMON[@]}" --set api.fault=empty_retrieval
wait_phase Degraded
[ "$(stable_hash)" = "$GOOD" ] || { echo "stable changed although the analysis failed"; exit 1; }
kubectl -n "$NS" get analysisrun
kubectl -n "$NS" get analysisrun -o jsonpath='{range .items[*]}{.metadata.name}{"  "}{.status.phase}{"  "}{.status.metricResults[0].measurements[*].value}{"\n"}{end}'

echo "== 3. stable build still serves"
smoke 0.75

echo "== 4. back to the good build, then a good change is promoted"
helm upgrade "$REL" "$CHART" -n "$NS" "${COMMON[@]}"
wait_phase Healthy
helm upgrade "$REL" "$CHART" -n "$NS" "${COMMON[@]}" --set api.env.RELEASE_NOTE=good-change
wait_promoted "$GOOD"
kubectl -n "$NS" get analysisrun -o jsonpath='{range .items[*]}{.metadata.name}{"  "}{.status.phase}{"\n"}{end}'
smoke 0.75
echo "canary drill passed"
