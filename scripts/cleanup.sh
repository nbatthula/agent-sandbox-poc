#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.rd/bin:$PATH"

POC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Cleaning up Agent Sandbox POC ==="

# Kill port-forward
if [[ -f /tmp/poc-portforward.pid ]]; then
    PID=$(cat /tmp/poc-portforward.pid | cut -d= -f2)
    kill $PID 2>/dev/null || true
    rm /tmp/poc-portforward.pid
fi

# Delete orchestrator
echo "Deleting orchestrator..."
kubectl delete -f "$POC_DIR/k8s/20-orchestrator.yaml" --ignore-not-found=true

# Delete warm pool and template
echo "Deleting warm pool and template..."
kubectl delete -f "$POC_DIR/k8s/10-sandbox-template-and-pool.yaml" --ignore-not-found=true

# Delete agent-sandbox
echo "Deleting agent-sandbox..."
kubectl delete -f "$POC_DIR/k8s/00-agent-sandbox.yaml" --ignore-not-found=true

# Delete images
echo "Removing local images..."
docker rmi sandbox-base:local orchestrator:local 2>/dev/null || true

# Clean up any leftover sandboxes/claims
echo "Cleaning up leftover sandboxes and claims..."
kubectl delete sandboxes --all -n default --ignore-not-found=true
kubectl delete sandboxclaims --all -n default --ignore-not-found=true

echo "=== Cleanup Complete ==="