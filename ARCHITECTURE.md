# Architecture Diagrams

## System Architecture (Mermaid)

```mermaid
graph TB
    subgraph Client["Client Applications"]
        DA[DeepAgents Service]
        LG[LangGraph Agents]
        CA[Custom Agents]
        CLI[CLI/IDE Extensions]
    end

    subgraph API["Orchestrator Layer"]
        ORCH[Orchestrator API :8080]
        SM[Session Manager]
        SC[Sandbox Claimer]
        TQ[Task Queue]
        CP[Checkpoint/Restore]
    end

    subgraph K8s["Kubernetes Control Plane"]
        ASC[agent-sandbox Controller]
        SCRD[Sandbox CRD]
        CCRD[SandboxClaim CRD]
        WPRD[SandboxWarmPool CRD]
        TRD[SandboxTemplate CRD]
    end

    subgraph Runtime["Sandbox Runtime"]
        WP[Warm Pool<br/>3 pre-warmed]
        SP1[Sandbox Pod 1]
        SP2[Sandbox Pod 2]
        SP3[Sandbox Pod 3]
        SN[New Sandbox<br/>on claim]
    end

    subgraph Storage["Storage"]
        EMPTY[emptyDir Volumes]
        PVC[PVC Templates<br/>Future]
        S3[S3 Checkpoints<br/>Future]
    end

    Client -->|REST| ORCH
    ORCH --> SM
    SM --> SC
    SC -->|Create SandboxClaim| K8s
    K8s -->|Bind to Warm Pool| WP
    WP -->|Adopt| SP1
    WP -->|Adopt| SP2
    WP -->|Adopt| SP3
    WP -.->|Create New| SN
    SP1 --> EMPTY
    SP2 --> EMPTY
    SP3 --> EMPTY
    SN --> PVC
    CP -.-> S3
```

## Sequence Diagram: Session Lifecycle

```mermaid
sequenceDiagram
    participant Client
    participant Orchestrator
    participant K8sAPI
    participant WarmPool
    participant Sandbox

    Client->>Orchestrator: POST /sessions {task}
    Orchestrator->>K8sAPI: Create SandboxClaim
    K8sAPI->>WarmPool: Find ready sandbox
    WarmPool->>Sandbox: Adopt existing pod
    Sandbox-->>K8sAPI: Ready (conditions: Ready=True)
    K8sAPI-->>Orchestrator: Claim bound, sandbox name
    Orchestrator->>Orchestrator: Update session RUNNING
    Orchestrator-->>Client: Session ID + status

    Note over Orchestrator,Sandbox: Session keeps sandbox alive

    Client->>Orchestrator: DELETE /sessions/{id}
    Orchestrator->>K8sAPI: Delete SandboxClaim
    K8sAPI->>WarmPool: Release sandbox
    WarmPool->>Sandbox: Delete pod
    WarmPool->>WarmPool: Create replacement
```

## Sandbox State Machine

```mermaid
stateDiagram-v2
    [*] --> Pending: Create Session
    Pending --> ClaimingSandbox: Start workflow
    ClaimingSandbox --> Running: Claim bound + Pod Ready
    ClaimingSandbox --> Failed: Claim timeout/failed
    Running --> Interrupted: Client cancels
    Running --> Completed: Task finishes (future)
    Running --> Failed: Error in execution
    Interrupted --> [*]: Release sandbox
    Completed --> [*]: Release sandbox
    Failed --> [*]: Release sandbox

    note right of Running
        In current POC: stays RUNNING
        until cancelled (no auto-complete)
    end note
```

## Warm Pool Replenishment

```mermaid
graph LR
    subgraph "Steady State (3 ready)"
        WP[WarmPool: 3/3 ready]
        S1[agent-warm-pool-aaa]
        S2[agent-warm-pool-bbb]
        S3[agent-warm-pool-ccc]
    end

    subgraph "Claim Request"
        CL[SandboxClaim]
    end

    subgraph "After Claim (2 ready + 1 claimed)"
        WP2[WarmPool: 2/3 ready]
        S1a[agent-warm-pool-aaa<br/>CLAIMED]
        S2a[agent-warm-pool-bbb]
        S3a[agent-warm-pool-ccc]
        NEW[agent-warm-pool-ddd<br/>CREATING]
    end

    subgraph "After Release (3 ready)"
        WP3[WarmPool: 3/3 ready]
        S1b[agent-warm-pool-eee]
        S2b[agent-warm-pool-bbb]
        S3b[agent-warm-pool-ccc]
    end

    WP --> CL
    CL --> WP2
    WP2 -->|Auto-create| NEW
    NEW -.->|Ready| WP3
    S1a -.->|Release| S1b
```

## Resource Requirements

```mermaid
graph TB
    subgraph Node["Single Node (Rancher Desktop)"]
        CPU["CPU: 8 cores"]
        MEM["RAM: 16 GiB"]
        
        subgraph System["System Pods"]
            KUBE[kube-system]
            ASC[agent-sandbox-controller]
        end
        
        subgraph WarmPool["Warm Pool (3 sandboxes)"]
            SB1[Sandbox 1: 500m CPU, 1Gi RAM]
            SB2[Sandbox 2: 500m CPU, 1Gi RAM]
            SB3[Sandbox 3: 500m CPU, 1Gi RAM]
        end
        
        subgraph Orchestrator["Orchestrator"]
            ORC[Orchestrator: 100m CPU, 128Mi RAM]
        end
    end
    
    SB1 -->|500m/1Gi| CPU
    SB2 -->|500m/1Gi| CPU
    SB3 -->|500m/1Gi| CPU
    ORC -->|100m/128Mi| CPU
    ASC -->|100m/64Mi| CPU
    KUBE -.->|~500m/1Gi| CPU
```

## Production EKS Architecture

```mermaid
graph TB
    subgraph AWS["AWS Cloud"]
        subgraph ControlPlane["EKS Control Plane"]
            EKS[EKS API Server]
        end
        
        subgraph NodeGroups["Managed Node Groups"]
            NG1[Node Group: General<br/>t3.medium x3<br/>Warm Pool: 10-20]
            NG2[Node Group: Compute<br/>c5.xlarge x2<br/>Heavy Tasks]
            NG3[Node Group: Mac<br/>mac1.metal x1<br/>iOS Builds]
        end
        
        subgraph Storage["Storage"]
            EBS[EBS CSI Driver<br/>GP3 Volumes]
            S3[S3 Bucket<br/>Checkpoints/Artifacts]
            ECR[ECR<br/>Container Images]
        end
        
        subgraph Network["Network"]
            VPC[VPC + Subnets]
            CILIUM[Cilium CNI<br/>NetworkPolicies]
            ALB[AWS Load Balancer<br/>Orchestrator API]
        end
        
        subgraph Data["Data"]
            RDS[(RDS PostgreSQL<br/>Sessions/Metadata)]
            REDIS[(ElastiCache Redis<br/>Queue/Cache)]
        end
        
        subgraph Observability["Observability"]
            OTEL[OpenTelemetry Collector]
            TEMPO[Tempo - Traces]
            LOKI[Loki - Logs]
            PROM[Prometheus - Metrics]
            GRAF[Grafana - Dashboards]
        end
    end
    
    Client[Client Apps] --> ALB
    ALB --> EKS
    EKS --> NG1
    EKS --> NG2
    EKS --> NG3
    NG1 --> EBS
    NG2 --> EBS
    NG3 --> EBS
    EKS --> RDS
    EKS --> REDIS
    EKS --> ECR
    OTEL --> TEMPO
    OTEL --> LOKI
    OTEL --> PROM
    PROM --> GRAF
    LOKI --> GRAF
    TEMPO --> GRAF
```

## Component Interaction Matrix

| From \ To | agent-sandbox Controller | Sandbox Pod | Warm Pool | Orchestrator | K8s API |
|-----------|-------------------------|-------------|-----------|--------------|---------|
| **Client** | - | - | - | REST API | - |
| **Orchestrator** | Watch/Create Claims | Exec (future) | Read Status | - | Full Access |
| **Controller** | - | Create/Manage | Reconcile | - | Full Access |
| **Warm Pool** | Create Sandboxes | Adopt Pods | - | - | - |
| **Sandbox Pod** | Report Status | - | - | Execute Tasks | - |

## Security Boundaries

```mermaid
graph TB
    subgraph "Trusted Zone"
        ORCH[Orchestrator]
        CTRL[Controller]
    end
    
    subgraph "Sandbox Zone (Isolated)"
        SB1[Sandbox A]
        SB2[Sandbox B]
        SB3[Sandbox C]
    end
    
    subgraph "Host/Node"
        K8S[K8s Node]
        GVISOR[gVisor/runsc]
    end
    
    ORCH -->|K8s API| CTRL
    CTRL -->|Create| SB1
    CTRL -->|Create| SB2
    CTRL -->|Create| SB3
    
    SB1 -.->|gVisor| GVISOR
    SB2 -.->|gVisor| GVISOR
    SB3 -.->|gVisor| GVISOR
    
    GVISOR -.->|Kernel| K8S
    
    style SB1 fill:#ffe6e6
    style SB2 fill:#ffe6e6
    style SB3 fill:#ffe6e6
    style GVISOR fill:#fff3e0
```