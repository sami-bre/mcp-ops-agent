#!/usr/bin/env bash
# Idempotent bootstrap for the k3d demo cluster (docker + k3d + kubectl + apps).
# Rebuilds the exact demo cluster: 1-node k3d cluster "ops-demo", namespace
# "demo", with web (nginx), redis, and a deliberately crash-looping payments-api.
set -euo pipefail

say() { echo "[bootstrap] $*"; }

# --- docker -----------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  say "installing docker (apt)..."
  apt-get update -qq
  apt-get install -y -qq docker.io
  systemctl enable --now docker 2>/dev/null || true
fi
if ! docker info >/dev/null 2>&1; then
  say "starting docker daemon..."
  (systemctl start docker 2>/dev/null || service docker start 2>/dev/null || dockerd >/dev/null 2>&1 &) 
  for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { say "ERROR: docker daemon not reachable"; exit 1; }
fi
say "docker ok: $(docker --version)"

# --- k3d --------------------------------------------------------------
if ! command -v k3d >/dev/null 2>&1; then
  say "installing k3d..."
  curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | TAG=v5.7.4 bash
fi
say "k3d ok: $(k3d version | head -1)"

# --- kubectl ----------------------------------------------------------
if ! command -v kubectl >/dev/null 2>&1; then
  say "installing kubectl..."
  curl -sL "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    -o /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl
fi
say "kubectl ok: $(kubectl version --client 2>/dev/null | head -1)"

# --- cluster ----------------------------------------------------------
if ! k3d cluster list | grep -q '^ops-demo '; then
  say "creating k3d cluster ops-demo..."
  k3d cluster create ops-demo \
    --servers 1 --agents 0 --wait \
    -p "8081:80@loadbalancer" \
    --k3s-arg "--disable=traefik@server:0"
else
  say "cluster ops-demo already exists"
fi

# wait for nodes ready
for i in $(seq 1 60); do
  kubectl get nodes 2>/dev/null | grep -q Ready && break
  sleep 2
done
kubectl get nodes

# --- demo apps --------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
kubectl apply -f "${SCRIPT_DIR}/manifests/00-namespace.yaml"
kubectl apply -f "${SCRIPT_DIR}/manifests/01-apps.yaml"
say "waiting for pods (payments-api is SUPPOSED to be CrashLoopBackOff)..."
sleep 20
kubectl get pods -n demo -o wide
say "done. 'kubectl get pods -n demo' should show web Running, redis Running,"
say "payments-api CrashLoopBackOff — real cluster state for the read-only tools."