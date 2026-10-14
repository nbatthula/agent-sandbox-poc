# Agent Sandbox POC - Makefile
# Run `make help` for available commands

.PHONY: help deploy test test-simple test-e2e clean logs status images build-images

# Default target
help:
	@echo "Agent Sandbox POC - Available Commands"
	@echo "======================================"
	@echo ""
	@echo "Build & Deploy:"
	@echo "  make build-images    - Build sandbox-base and orchestrator Docker images"
	@echo "  make deploy          - Deploy everything to Rancher Desktop k3s"
	@echo "  make deploy-full     - Build images + deploy (full deployment)"
	@echo ""
	@echo "Testing:"
	@echo "  make test            - Run simple verification tests (recommended)"
	@echo "  make test-simple     - Run simple test (alias for test)"
	@echo "  make test-e2e        - Run full E2E tests (requires exec fix)"
	@echo "  make test-deepagent  - Run DeepAgents-style research task test"
	@echo ""
	@echo "Operations:"
	@echo "  make status          - Show status of all components"
	@echo "  make logs            - Show orchestrator logs"
	@echo "  make logs-controller - Show agent-sandbox controller logs"
	@echo "  make port-forward    - Start port-forward for orchestrator API"
	@echo "  make stop-port-forward - Stop port-forward"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean           - Remove all deployed resources"
	@echo "  make clean-images    - Remove local Docker images"
	@echo "  make clean-all       - Clean everything (resources + images)"
	@echo ""
	@echo "Development:"
	@echo "  make dev-setup       - Install Python dependencies locally"
	@echo "  make fmt             - Format Python code with black"
	@echo "  make lint            - Lint Python code with ruff"

# Variables
KUBECTL := kubectl
DOCKER := docker
POC_DIR := $(CURDIR)
ORCHESTRATOR_IMAGE := orchestrator:local
SANDBOX_IMAGE := sandbox-base:local

# Build commands
build-images: build-sandbox build-orchestrator

build-sandbox:
	@echo "Building sandbox-base image..."
	$(DOCKER) build --platform linux/arm64 -t $(SANDBOX_IMAGE) -f images/sandbox-base/Dockerfile images/sandbox-base/

build-orchestrator:
	@echo "Building orchestrator image..."
	$(DOCKER) build -t $(ORCHESTRATOR_IMAGE) orchestrator/

# Deploy commands
deploy: deploy-agent-sandbox deploy-template deploy-pool deploy-orchestrator wait-ready port-forward
	@echo ""
	@echo "✅ Deployment complete!"
	@echo "Orchestrator API: http://localhost:8080"
	@echo "Run 'make test' to verify"

deploy-full: build-images deploy

deploy-agent-sandbox:
	@echo "Deploying agent-sandbox CRDs and controller..."
	$(KUBECTL) apply -f k8s/00-agent-sandbox.yaml
	$(KUBECTL) wait --for=condition=available deployment/agent-sandbox-controller -n agent-sandbox-system --timeout=120s

deploy-template:
	@echo "Deploying SandboxTemplate..."
	$(KUBECTL) apply -f k8s/10-sandbox-template-and-pool.yaml

deploy-pool:
	@echo "Deploying SandboxWarmPool..."
	$(KUBECTL) apply -f k8s/10-sandbox-template-and-pool.yaml

deploy-orchestrator:
	@echo "Deploying Orchestrator..."
	$(KUBECTL) apply -f k8s/20-orchestrator.yaml
	$(KUBECTL) wait --for=condition=available deployment/orchestrator -n orchestrator-system --timeout=60s

wait-ready:
	@echo "Waiting for warm pool to be ready..."
	@for i in $$(seq 1 30); do \
		READY=$$($(KUBECTL) get sandboxwarmpool agent-warm-pool -n default -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0"); \
		DESIRED=$$($(KUBECTL) get sandboxwarmpool agent-warm-pool -n default -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "3"); \
		echo "  Warm pool: $$READY/$$DESIRED ready"; \
		if [ "$$READY" = "$$DESIRED" ] && [ "$$READY" != "" ]; then \
			echo "  ✓ Warm pool ready"; \
			break; \
		fi; \
		sleep 5; \
	done

port-forward:
	@echo "Starting port-forward for orchestrator API..."
	@pkill -f "port-forward.*orchestrator" 2>/dev/null || true
	@$(KUBECTL) port-forward -n orchestrator-system svc/orchestrator 8080:8080 > /tmp/orchestrator-portforward.log 2>&1 & \
	sleep 3; \
	if curl -s http://localhost:8080/health | grep -q healthy; then \
		echo "  ✓ Orchestrator API reachable at http://localhost:8080"; \
	else \
		echo "  ✗ Orchestrator API not reachable"; \
		exit 1; \
	fi

# Test commands
test: test-simple

test-simple:
	@echo "Running simple verification tests..."
	@cd $(POC_DIR) && python3 tests/test_simple.py

test-e2e:
	@echo "Running full E2E tests..."
	@cd $(POC_DIR) && RUN_FULL_TEST=1 RUN_PARALLEL_TEST=1 python3 tests/test_e2e.py

test-deepagent:
	@echo "Running DeepAgents-style test..."
	@cd $(POC_DIR) && python3 tests/test_deepagent_task.py

# Status commands
status:
	@echo "=== Cluster Status ==="
	@$(KUBECTL) get nodes -o wide
	@echo ""
	@echo "=== Agent Sandbox Controller ==="
	@$(KUBECTL) get pods -n agent-sandbox-system
	@echo ""
	@echo "=== Sandbox Resources ==="
	@$(KUBECTL) get sandboxwarmpool,sandboxtemplate -n default
	@$(KUBECTL) get sandboxes -n default
	@$(KUBECTL) get sandboxclaims -n default
	@echo ""
	@echo "=== Orchestrator ==="
	@$(KUBECTL) get pods,svc -n orchestrator-system

logs:
	@$(KUBECTL) logs -n orchestrator-system -l app=orchestrator --tail=100 -f

logs-controller:
	@$(KUBECTL) logs -n agent-sandbox-system -l app=agent-sandbox-controller --tail=100 -f

# Port forward management
stop-port-forward:
	@pkill -f "port-forward.*orchestrator" 2>/dev/null || true
	@echo "Port-forward stopped"

# Cleanup commands
clean:
	@echo "Cleaning up Kubernetes resources..."
	$(KUBECTL) delete -f k8s/20-orchestrator.yaml --ignore-not-found=true
	$(KUBECTL) delete -f k8s/10-sandbox-template-and-pool.yaml --ignore-not-found=true
	$(KUBECTL) delete -f k8s/00-agent-sandbox.yaml --ignore-not-found=true
	$(KUBECTL) delete sandboxes --all -n default --ignore-not-found=true
	$(KUBECTL) delete sandboxclaims --all -n default --ignore-not-found=true
	@pkill -f "port-forward.*orchestrator" 2>/dev/null || true

clean-images:
	@echo "Removing local Docker images..."
	$(DOCKER) rmi $(SANDBOX_IMAGE) $(ORCHESTRATOR_IMAGE) 2>/dev/null || true

clean-all: clean clean-images

# Development
dev-setup:
	@echo "Installing Python dependencies..."
	@cd orchestrator && pip3 install -r requirements.txt

fmt:
	@black orchestrator/main.py tests/*.py 2>/dev/null || echo "black not installed"

lint:
	@ruff check orchestrator/main.py tests/*.py 2>/dev/null || echo "ruff not installed"