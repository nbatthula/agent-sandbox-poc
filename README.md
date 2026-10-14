# Agent Sandbox POC

> **Self-hosted AI agent sandbox infrastructure on Kubernetes**  
> Proof-of-concept running locally on Rancher Desktop, production-ready for AWS EKS

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-1.28%2B-blue.svg)](https://kubernetes.io/)
[![agent-sandbox](https://img.shields.io/badge/agent--sandbox-v1.0.6-green.svg)](https://github.com/kubernetes-sigs/agent-sandbox)

## Quick Start

```bash
# Prerequisites: Rancher Desktop with Kubernetes enabled
export PATH="$HOME/.rd/bin:$PATH"

# Clone and deploy
cd agent-sandbox-poc
make deploy-full

# Verify it works
make test

# Test the API
curl http://localhost:8080/health
curl -X POST http://localhost:8080/sessions \
  -H 'Content-Type: application/json' \
  -d '{"name":"test","commands":["echo hello","python3 -c \"print(42)\""]}'
```

## What This POC Demonstrates

| Feature | Status | Description |
|---------|--------|-------------|
| **Warm Pool** | ✅ Working | 3 pre-warmed sandboxes, instant allocation (< 5s) |
| **Sandbox CRD** | ✅ Working | Kubernetes-native sandbox lifecycle management |
| **Claim/Release** | ✅ Working | SandboxClaim CRD binds/releases from warm pool |
| **Orchestrator API** | ✅ Working | FastAPI service for session management |
| **Parallel Claims** | ✅ Working | Multiple concurrent sessions (resource-limited) |
| **gVisor Ready** | 🔧 Configured | RuntimeClass set, needs `runsc` on nodes |
| **Command Execution** | ⚠️ Partial | kubectl exec timing issues with warm pool reclaim |

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  Client     │────▶│  Orchestrator    │────▶│  agent-sandbox      │
│  (REST)     │     │  (FastAPI)       │     │  Controller         │
└─────────────┘     └────────┬─────────┘     └──────────┬──────────┘
                             │                          │
                    ┌────────┴────────┐     ┌───────────┴────────┐
                    ▼                 ▼     ▼                    ▼
              Session Store    SandboxClaim    Warm Pool (3)    Sandbox Pods
              (In-Memory)      (CRD)           (3 ready)        (gVisor-ready)
```

## Project Structure

```
agent-sandbox-poc/
├── Makefile                      # All build/deploy/test commands
├── README.md                     # This file
├── PROJECT.md                    # Detailed project specification
├── ARCHITECTURE.md               # Architecture diagrams (Mermaid)
├── POC_SUMMARY.md                # Executive summary
├── k8s/
│   ├── 00-agent-sandbox.yaml     # CRDs + Controller (from upstream v1.0.6)
│   ├── 10-sandbox-template-and-pool.yaml  # SandboxTemplate + WarmPool
│   └── 20-orchestrator.yaml      # Orchestrator deployment + RBAC
├── images/
│   └── sandbox-base/
│       └── Dockerfile            # Ubuntu 24.04 + Python/Node + agent libs
├── orchestrator/
│   ├── Dockerfile
│   ├── main.py                   # FastAPI orchestrator
│   └── requirements.txt
├── scripts/
│   ├── build-sandbox-image.sh
│   ├── deploy.sh
│   └── cleanup.sh
└── tests/
    ├── test_simple.py            # ✅ Working verification test
    ├── test_e2e.py               # Full E2E (needs exec fix)
    └── test_deepagent_task.py    # DeepAgents-style simulation
```

## Commands

```bash
# Build & Deploy
make build-images          # Build Docker images
make deploy                # Deploy to Rancher Desktop k3s
make deploy-full           # Build + Deploy (recommended)

# Testing
make test                  # Run simple verification tests
make test-e2e              # Run full E2E tests
make test-deepagent        # Run DeepAgents-style test

# Operations
make status                # Show all component status
make logs                  # Tail orchestrator logs
make logs-controller       # Tail agent-sandbox controller logs
make port-forward          # Start API port-forward
make stop-port-forward     # Stop port-forward

# Cleanup
make clean                 # Remove K8s resources
make clean-images          # Remove Docker images
make clean-all             # Clean everything
```

## API Reference

### Health Check
```bash
GET /health
# {"status":"healthy","orchestrator":"running"}
```

### Create Session (Claims Sandbox)
```bash
POST /sessions
Content-Type: application/json

{
  "name": "research-task",
  "description": "Analyze codebase",
  "repo_url": "https://github.com/example/repo",
  "repo_branch": "main",
  "commands": ["cd /workspace/repo && python3 analyze.py"],
  "timeout_seconds": 600
}
```

### Get Session Status
```bash
GET /sessions/{session_id}
# Returns: status, sandbox_name, started_at, result, error
```

### Cancel Session (Releases Sandbox)
```bash
DELETE /sessions/{session_id}
```

## Session Lifecycle

```
PENDING → CLAIMING_SANDBOX → RUNNING → (INTERRUPTED|COMPLETED|FAILED)
                              ↓
                         [Keeps sandbox alive]
                              ↓
                         FINALLY: Release to warm pool
```

## Verified Test Results

```
=== Checking Warm Pool Status ===
Warm pool status: 3/3

=== Testing Session Creation with Warm Pool ===
✓ Session created: abc12345
  Status: pending → running
✓ Sandbox claimed: agent-warm-pool-xyz789
✓ Sandbox agent-warm-pool-xyz789 is Ready in Kubernetes
✓ Session cancelled, sandbox released back to warm pool

=== Testing Parallel Sandbox Claims ===
Warm pool has 3 ready sandboxes, testing with 2
✓ All 2 parallel claims succeeded
```

## Known Limitations (POC)

| Limitation | Production Fix |
|------------|----------------|
| Single-node k3s resource limits | EKS multi-node |
| No gVisor installed (using runc) | Install runsc on EKS nodes |
| In-memory session store | Redis/PostgreSQL |
| No kubectl exec in orchestrator | Agent SDK or fix exec |
| No credential vault | OpenSandbox vault or custom |
| No egress control | Cilium NetworkPolicies |
| No snapshot/restore | PVC + CSI snapshots |

## Production Roadmap (AWS EKS)

1. **Foundation** - EKS cluster, gVisor, RDS, ECR, warm pool baseline
2. **Agent Integration** - Agent SDK, DeepAgents/LangGraph, checkpoint/restore
3. **Security** - Egress policies, NetworkPolicies, audit logging
4. **Mac Fleet** - EC2 Mac Dedicated Hosts for iOS/Xcode
5. **Observability** - OpenTelemetry, Tempo, Loki, Prometheus, Grafana
6. **Scale** - KEDA autoscaling, multi-region, cost optimization

## Cost Estimate (AWS EKS)

| Component | Monthly Cost |
|-----------|-------------|
| EKS Control Plane | $73 |
| Worker Nodes (3× t3.medium) | ~$200 |
| Warm Pool (10× t3.small) | ~$150 |
| RDS PostgreSQL | ~$50 |
| ECR Storage | ~$5 |
| **Total** | **~$478/month** |
| **Per Sandbox/Hour** | **~$0.06** |
| **Mac Host (M1)** | **$1.20/hr** ($864/mo min 24hr) |

## Documentation

- [PROJECT.md](PROJECT.md) - Detailed project specification
- [ARCHITECTURE.md](ARCHITECTURE.md) - Architecture diagrams (Mermaid)
- [POC_SUMMARY.md](POC_SUMMARY.md) - Executive summary

## References

- [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) - Kubernetes SIG project
- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox) - Alternative with vault/egress
- [gVisor](https://gvisor.dev/) - Container sandbox runtime
- [EKS Best Practices](https://aws.github.io/aws-eks-best-practices/)

## License

Apache 2.0 - See [LICENSE](LICENSE) for details.