#!/usr/bin/env bash
set -euo pipefail

# Deploy everything to Rancher Desktop Kubernetes

export PATH="$HOME/.rd/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POC_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Deploying Agent Sandbox POC to Rancher Desktop ==="

# 1. Build sandbox base image
echo ""
echo "1. Building sandbox base image..."
"$POC_DIR/scripts/build-sandbox-image.sh"

# 2. Deploy agent-sandbox CRDs and controller
echo ""
echo "2. Deploying agent-sandbox..."
kubectl apply -f "$POC_DIR/k8s/00-agent-sandbox.yaml"

# Wait for controller to be ready
echo "   Waiting for agent-sandbox controller..."
kubectl wait --for=condition=available deployment/agent-sandbox-controller \
    -n agent-sandbox-system --timeout=120s

# 3. Deploy sandbox template and warm pool
echo ""
echo "3. Deploying sandbox template and warm pool..."
kubectl apply -f "$POC_DIR/k8s/10-sandbox-template-and-pool.yaml"

# Wait for warm pool to be ready
echo "   Waiting for warm pool to populate (this may take 60-120s)..."
for i in {1..24}; do
    READY=$(kubectl get sandboxwarmpool agent-warm-pool -n default -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    DESIRED=$(kubectl get sandboxwarmpool agent-warm-pool -n default -o jsonpath='{.spec.poolSize}' 2>/dev/null || echo "3")
    if [[ "$READY" == "$DESIRED" && "$READY" != "" ]]; then
        echo "   ✓ Warm pool ready: $READY/$DESIRED sandboxes"
        break
    fi
    echo "   Warm pool: $READY/$DESIRED ready (waiting...)"
    sleep 5
done

# 4. Build and load orchestrator image
echo ""
echo "4. Building orchestrator image..."
docker build -t orchestrator:local "$POC_DIR/orchestrator/"

# 5. Deploy orchestrator
echo ""
echo "5. Deploying orchestrator..."
kubectl apply -f "$POC_DIR/k8s/20-orchestrator.yaml"

# Wait for orchestrator
echo "   Waiting for orchestrator..."
kubectl wait --for=condition=available deployment/orchestrator \
    -n orchestrator-system --timeout=60s

# 6. Port-forward orchestrator for local access
echo ""
echo "6. Starting port-forward for orchestrator API..."
kubectl port-forward -n orchestrator-system svc/orchestrator 8080:8080 > /tmp/orchestrator-portforward.log 2>&1 &
PORTFORWARD_PID=$!
sleep 3

# Verify orchestrator is reachable
if curl -s http://localhost:8080/health | grep -q "healthy"; then
    echo "   ✓ Orchestrator API reachable at http://localhost:8080"
else
    echo "   ✗ Orchestrator API not reachable"
    kill $PORTFORWARD_PID 2>/dev/null || true
    exit 1
fi

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Resources:"
echo "  - agent-sandbox controller: agent-sandbox-system namespace"
echo "  - SandboxTemplate: agent-sandbox-template (default namespace)"
echo "  - SandboxWarmPool: agent-warm-pool (default namespace, 3 sandboxes)"
echo "  - Orchestrator: orchestrator-system namespace, port-forwarded to localhost:8080"
echo ""
echo "Test the API:"
echo "  curl http://localhost:8080/health"
echo "  curl -X POST http://localhost:8080/sessions -H 'Content-Type: application/json' -d '{\"name\":\"test\",\"commands\":[\"echo hello\"]}'"
echo ""
echo "Run tests:"
echo "  cd $POC_DIR && python tests/test_e2e.py"
echo ""
echo "To stop port-forward: kill $PORTFORWARD_PID"
echo "PORTFORWARD_PID=$PORTFORWARD_PID" > /tmp/poc-portforward.pid