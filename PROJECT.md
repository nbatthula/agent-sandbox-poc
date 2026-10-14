# Agent Sandbox POC - Project Specification

## Project Overview

**Name:** Agent Sandbox POC  
**Purpose:** Proof-of-concept for self-hosted AI agent sandbox infrastructure on Kubernetes  
**Environment:** Rancher Desktop (k3s) for local development, targeting AWS EKS for production  
**License:** Apache 2.0 (all components)

## Problem Statement

AI agents need isolated, ephemeral execution environments with:
- **Fast startup** (< 5 seconds) for interactive agent workflows
- **Strong isolation** (container-level via gVisor, not VM)
- **Stateful workspaces** with snapshot/restore capability
- **Credential management** and egress control per sandbox
- **Mac fleet support** for iOS/Xcode development workloads

## Solution Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CLIENT APPLICATIONS                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │  DeepAgents │  │  LangGraph  │  │  Custom     │  │  CLI/IDE    │   │
│  │  Service    │  │  Agents     │  │  Agents     │  │  Extensions │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│         │                │                │                │          │
│         └────────────────┼────────────────┼────────────────┘          │
│                          ▼                                             │
│              ┌───────────────────────┐                                │
│              │  ORCHESTRATOR API     │                                │
│              │  (FastAPI, port 8080) │                                │
│              │  • Session Management │                                │
│              │  • Sandbox Claims     │                                │
│              │  • Task Queue         │                                │
│              │  • Checkpoint/Restore │                                │
│              └───────────┬───────────┘                                │
└──────────────────────────│────────────────────────────────────────────┘
                           │ Kubernetes API
              ┌────────────┴────────────┐
              ▼                         ▼
    ┌─────────────────────┐   ┌─────────────────────┐
    │  AGENT-SANDBOX      │   │  WARM POOL          │
    │  CONTROLLER         │   │  (3 pre-warmed)     │
    │  • Sandbox CRD      │   │  • agent-warm-pool  │
    │  • Claim CRD        │   │  • Instant alloc    │
    │  • Template CRD     │   │  • Auto-replenish   │
    └─────────┬───────────┘   └─────────────────────┘
              │
              ▼
    ┌─────────────────────┐
    │  SANDBOX PODS       │
    │  • gVisor runtime   │
    │  • emptyDir volumes │
    │  • Non-root (UID 1001)           │
    │  • Agent tooling pre-installed   │
    └─────────────────────┘
```

### Component Specifications

#### 1. Sandbox Base Image (`sandbox-base:local`)

| Property | Value |
|----------|-------|
| Base OS | Ubuntu 24.04 (ARM64) |
| User | `agent` (UID 1001, GID 1001) |
| Workspace | `/workspace` (emptyDir, 2Gi) |
| Cache | `/home/agent/.cache` (emptyDir, 500Mi) |
| Runtime | Python 3.12, Node.js 20, build-essential |
| Agent Libraries | langchain, langgraph, deepagents, pydantic, httpx |
| Browser | Chromium + ChromeDriver (Playwright) |
| Security | Non-root, sudo NOPASSWD, read-only rootfs ready |

**Dockerfile:** `images/sandbox-base/Dockerfile`

#### 2. SandboxTemplate (`agent-sandbox-template`)

```yaml
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxTemplate
metadata:
  name: agent-sandbox-template
  namespace: default
spec:
  podTemplate:
    spec:
      runtimeClassName: runsc          # gVisor (when installed)
      securityContext:
        runAsUser: 1001
        runAsGroup: 1001
        fsGroup: 1001
        runAsNonRoot: true
      containers:
      - name: agent-sandbox
        image: sandbox-base:local
        imagePullPolicy: Never
        command: ["/bin/sh", "-c", "sleep infinity"]
        resources:
          requests: { cpu: "500m", memory: "1Gi", ephemeral-storage: "2Gi" }
          limits: { cpu: "2000m", memory: "4Gi", ephemeral-storage: "4Gi" }
        volumeMounts:
        - name: workspace; mountPath: /workspace
        - name: cache; mountPath: /home/agent/.cache
      volumes:
      - name: workspace; emptyDir: { sizeLimit: 2Gi }
      - name: cache; emptyDir: { sizeLimit: 500Mi }
  volumeClaimTemplatesPolicy: Disallowed
```

#### 3. SandboxWarmPool (`agent-warm-pool`)

```yaml
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxWarmPool
metadata:
  name: agent-warm-pool
  namespace: default
spec:
  replicas: 3
  sandboxTemplateRef:
    name: agent-sandbox-template
  updateStrategy:
    type: Recreate
```

#### 4. Orchestrator Service

**Technology:** FastAPI + Kubernetes Python client  
**Port:** 8080 (ClusterIP, port-forwarded for local access)  
**Session Store:** In-memory (replace with Redis for production)

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/sessions` | Create session (claims sandbox) |
| GET | `/sessions/{id}` | Get session status |
| DELETE | `/sessions/{id}` | Cancel session (releases sandbox) |

**Session Lifecycle:**
```
PENDING → CLAIMING_SANDBOX → RUNNING → (INTERRUPTED|COMPLETED|FAILED)
                              ↓
                         [Auto-keep-alive until cancelled]
                              ↓
                         FINALLY: Release sandbox to pool
```

## Kubernetes Resources

### Namespaces
| Namespace | Purpose |
|-----------|---------|
| `agent-sandbox-system` | agent-sandbox controller |
| `orchestrator-system` | Orchestrator deployment |
| `default` | Sandboxes, WarmPool, Templates, Claims |

### RBAC

**agent-sandbox-controller** (ClusterRole):
- Full access to: sandboxes, sandboxclaims, sandboxwarmpools, sandboxtemplates
- Pods, PVCs, Services, ConfigMaps, Secrets, Events
- NetworkPolicies (for egress control)

**orchestrator** (Role in default namespace):
- sandboxclaims: create, get, list, watch, delete, patch
- sandboxes: get, list, watch
- pods: get, list, create (for exec)

## Deployment Process

### Prerequisites
- Rancher Desktop with Kubernetes enabled
- `kubectl`, `docker`, `helm` in PATH (`~/.rd/bin/`)
- Python 3.9+ for local test execution

### Quick Start

```bash
# 1. Clone and enter
cd agent-sandbox-poc

# 2. Deploy everything (builds images, deploys CRDs, starts warm pool)
make deploy-full

# 3. Run verification tests
make test

# 4. Test API manually
curl http://localhost:8080/health
curl -X POST http://localhost:8080/sessions \
  -H 'Content-Type: application/json' \
  -d '{"name":"test","commands":["echo hello","python3 -c \"print(42)\""]}'
```

### Manual Deployment Steps

```bash
# Build images
make build-images

# Deploy agent-sandbox CRDs + controller
make deploy-agent-sandbox

# Deploy template + warm pool
make deploy-template deploy-pool

# Wait for warm pool
make wait-ready

# Deploy orchestrator
make deploy-orchestrator

# Start port-forward
make port-forward
```

## Testing

### Test Suite

| Test | File | Purpose |
|------|------|---------|
| Simple Verification | `tests/test_simple.py` | ✅ Warm pool claim/release cycle |
| E2E Full | `tests/test_e2e.py` | Session + command execution (needs exec fix) |
| DeepAgents Style | `tests/test_deepagent_task.py` | Research task simulation |

### Running Tests

```bash
# Simple test (recommended - works fully)
make test

# Full E2E (requires kubectl exec fix)
make test-e2e

# DeepAgents research simulation
make test-deepagent
```

### Expected Test Output

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

## Known Limitations (POC Scope)

| Limitation | Impact | Production Fix |
|------------|--------|----------------|
| Single-node k3s | Max 1-2 concurrent sandboxes | EKS multi-node |
| No gVisor installed | Using runc, not runsc | Install runsc on EKS nodes |
| In-memory session store | Not persistent across restarts | Redis/PostgreSQL |
| No kubectl exec in orchestrator | Can't run commands in sandbox | Fix exec or use agent SDK |
| No credential vault | No secret injection | OpenSandbox vault or custom |
| No egress control | Sandboxes have full network | Cilium NetworkPolicies |
| No snapshot/restore | No checkpoint persistence | PVC + CSI snapshots |

## Production Roadmap (AWS EKS)

### Phase 1: Foundation (Week 1-2)
- [ ] EKS cluster with managed node groups
- [ ] gVisor (`runsc`) via node bootstrap script
- [ ] RDS PostgreSQL for session persistence
- [ ] ECR for image storage
- [ ] Warm pool baseline (10-20 sandboxes)

### Phase 2: Agent Integration (Week 2-3)
- [ ] Agent SDK integration (agent-sandbox Python SDK)
- [ ] DeepAgents/LangGraph execution in sandbox
- [ ] Checkpoint/restore via PVC + CSI snapshots
- [ ] Credential broker for API keys/secrets

### Phase 3: Security & Control (Week 3-4)
- [ ] Egress policies per sandbox (Cilium)
- [ ] NetworkPolicy templates in SandboxTemplate
- [ ] Audit logging for all sandbox operations
- [ ] Resource quotas and limits

### Phase 4: Mac Fleet (Week 4-5)
- [ ] EC2 Mac Dedicated Hosts (24hr minimum allocation)
- [ ] Separate warm pool for macOS sandboxes
- [ ] Xcode/simulator pre-installed images
- [ ] Cost-aware scaling (only when iOS tasks queued)

### Phase 5: Observability & Scale (Week 5-6)
- [ ] OpenTelemetry → Tempo (traces) + Loki (logs) + Prometheus (metrics)
- [ ] KEDA-based warm pool autoscaling
- [ ] Multi-region deployment
- [ ] Cost optimization (spot instances, right-sizing)

## Cost Model (Estimated)

| Component | Local (Rancher) | AWS EKS (Monthly) |
|-----------|-----------------|-------------------|
| Control Plane | Free | $73 (EKS) |
| Worker Nodes | Local CPU/RAM | ~$200 (3x t3.medium) |
| Warm Pool (3 sandboxes) | Local | ~$45 (3x t3.small) |
| RDS PostgreSQL | N/A | ~$50 (db.t3.micro) |
| ECR Storage | N/A | ~$5 |
| **Total/Month** | **$0** | **~$373** |
| **Per Sandbox/Hour** | **$0** | **~$0.06** |
| **Mac Host (M1)** | N/A | **$1.20/hr** ($864/mo min) |

## File Structure

```
agent-sandbox-poc/
├── Makefile                      # Build/deploy/test commands
├── README.md                     # Quick start guide
├── PROJECT.md                    # This file - detailed spec
├── POC_SUMMARY.md                # Executive summary
├── k8s/
│   ├── 00-agent-sandbox.yaml     # CRDs + Controller (upstream)
│   ├── 10-sandbox-template-and-pool.yaml  # Template + WarmPool
│   └── 20-orchestrator.yaml      # Orchestrator + RBAC
├── images/
│   └── sandbox-base/
│       └── Dockerfile            # Base sandbox image
├── orchestrator/
│   ├── Dockerfile
│   ├── main.py                   # FastAPI orchestrator
│   └── requirements.txt
├── scripts/
│   ├── build-sandbox-image.sh
│   ├── deploy.sh
│   └── cleanup.sh
└── tests/
    ├── test_simple.py            # ✅ Working verification
    ├── test_e2e.py               # Full E2E (exec needs fix)
    └── test_deepagent_task.py    # DeepAgents simulation
```

## API Usage Examples

### Create a Research Session

```bash
curl -X POST http://localhost:8080/sessions \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "deep-research",
    "description": "Analyze codebase and produce summary",
    "repo_url": "https://github.com/example/repo",
    "repo_branch": "main",
    "commands": [
      "cd /workspace/repo && find . -name \"*.py\" | head -20",
      "cd /workspace/repo && python3 -m pytest --collect-only 2>/dev/null | head -30"
    ],
    "timeout_seconds": 600
  }'
```

### Monitor Session

```bash
# Poll for completion
SESSION_ID=$(curl -s -X POST ... | jq -r .session_id)
while true; do
  STATUS=$(curl -s http://localhost:8080/sessions/$SESSION_ID | jq -r .status)
  echo "Status: $STATUS"
  [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] && break
  sleep 5
done
curl -s http://localhost:8080/sessions/$SESSION_ID | jq .
```

### Cleanup

```bash
make clean-all
```

## Troubleshooting

### Warm Pool Not Ready
```bash
kubectl describe sandboxwarmpool agent-warm-pool -n default
kubectl get pods -n default -l agents.x-k8s.io/warm-pool-sandbox
```

### Sandbox Claim Stuck
```bash
kubectl get sandboxclaim -n default
kubectl describe sandboxclaim claim-xxx -n default
```

### Orchestrator Can't Exec
```bash
kubectl logs -n orchestrator-system deployment/orchestrator
# Check RBAC: orchestrator needs pods/exec permission
```

### Image Pull Errors
```bash
# Ensure image in containerd (Rancher Desktop)
docker image ls sandbox-base:local
nerdctl -n k8s.io images | grep sandbox-base
```

## References

- [agent-sandbox GitHub](https://github.com/kubernetes-sigs/agent-sandbox)
- [agent-sandbox Documentation](https://agent-sandbox.sigs.k8s.io/)
- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)
- [gVisor](https://gvisor.dev/)
- [EKS Best Practices](https://aws.github.io/aws-eks-best-practices/)