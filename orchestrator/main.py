from __future__ import annotations

import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from enum import Enum

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
import httpx
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, String, Text, DateTime, JSON, Integer, Index, select, Boolean
from sqlalchemy.orm import declarative_base
import traceback

# Optional OpenTelemetry imports
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    HAVE_OTEL = True
except ImportError:
    HAVE_OTEL = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if HAVE_OTEL:
    try:
        resource = Resource(attributes={SERVICE_NAME: "orchestrator"})
        trace_provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(trace_provider)
        tracer = trace.get_tracer(__name__)

        meter = metrics.get_meter(__name__)
        sessions_created = meter.create_counter("sessions_created_total", description="Total sessions created")
        sessions_completed = meter.create_counter("sessions_completed_total", description="Total sessions completed")
        sessions_failed = meter.create_counter("sessions_failed_total", description="Total sessions failed")
        active_sessions = meter.create_up_down_counter("active_sessions", description="Currently active sessions")
        session_duration = meter.create_histogram("session_duration_seconds", description="Session duration in seconds")
        claim_duration = meter.create_histogram("sandbox_claim_duration_seconds", description="Time to claim sandbox")

        OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://otel-collector.observability:4317")
        trace_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True), export_interval_millis=30000)
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))
    except Exception as e:
        logger.warning(f"OTEL initialization skipped: {e}")
        tracer = None
else:
    tracer = None


class SessionStatus(str, Enum):
    PENDING = "pending"
    CLAIMING_SANDBOX = "claiming_sandbox"
    RUNNING = "running"
    CHECKPOINTING = "checkpointing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class SandboxClaimStatus(str, Enum):
    PENDING = "pending"
    BOUND = "bound"
    FAILED = "failed"
    RELEASED = "released"


@dataclass
class AgentTask:
    """Task to be executed by an agent in a sandbox."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    repo_url: str = ""
    repo_branch: str = "main"
    commands: List[str] = field(default_factory=list)
    env_vars: Dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 600
    auto_release: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Session:
    """Represents an agent session with a sandbox."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    task: Optional[AgentTask] = None
    status: SessionStatus = SessionStatus.PENDING
    sandbox_name: Optional[str] = None
    claim_name: Optional[str] = None
    sandbox_namespace: str = "default"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    checkpoint_data: Optional[Dict[str, Any]] = None


class Settings(BaseSettings):
    kubeconfig_path: Optional[str] = None
    namespace: str = "default"
    warm_pool_name: str = "agent-warm-pool"
    sandbox_template_name: str = "agent-sandbox-template"
    default_ttl_seconds: int = 3600
    max_concurrent_sessions: int = 5
    checkpoint_interval_seconds: int = 30
    orchestrator_port: int = 8080
    
    # Redis configuration
    redis_url: str = "redis://redis.redis:6379/0"
    
    # PostgreSQL configuration
    postgres_url: str = "postgresql+asyncpg://orchestrator:changeme123@postgres.postgres:5432/agent_sandbox"
    
    # OTel
    otlp_endpoint: str = "http://otel-collector.observability:4317"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# SQLAlchemy models
Base = declarative_base()

class SessionModel(Base):
    __tablename__ = "sessions"
    
    id = Column(String(16), primary_key=True)
    task_name = Column(String(255), nullable=False)
    task_description = Column(Text)
    repo_url = Column(Text)
    repo_branch = Column(String(100), default="main")
    commands = Column(JSON, default=list)
    env_vars = Column(JSON, default=dict)
    timeout_seconds = Column(Integer, default=600)
    auto_release = Column(Boolean, default=False)
    status = Column(String(50), nullable=False, default="pending")
    sandbox_name = Column(String(100))
    claim_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    result = Column(JSON)
    error = Column(Text)
    checkpoint_data = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_sessions_status", "status"),
        Index("idx_sessions_created_at", "created_at"),
    )


class SandboxClaimModel(Base):
    __tablename__ = "sandbox_claims"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(16), nullable=False)
    claim_name = Column(String(100), nullable=False)
    sandbox_name = Column(String(100), nullable=False)
    warm_pool_name = Column(String(100), nullable=False)
    claimed_at = Column(DateTime, default=datetime.utcnow)
    released_at = Column(DateTime)
    status = Column(String(50), nullable=False)
    
    __table_args__ = (
        Index("idx_sandbox_claims_session_id", "session_id"),
    )


class CheckpointModel(Base):
    __tablename__ = "checkpoints"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(16), nullable=False)
    checkpoint_type = Column(String(50), nullable=False)
    data = Column(JSON, nullable=False)
    s3_path = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_checkpoints_session_id", "session_id"),
    )


class RedisSessionStore:
    """Redis-backed session store for fast access with TTL."""
    
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
        self._lock = asyncio.Lock()
    
    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            async with self._lock:
                if self._redis is None:
                    self._redis = redis.from_url(self.redis_url, decode_responses=True)
        return self._redis
    
    def _session_key(self, session_id: str) -> str:
        return f"session:{session_id}"
    
    def _claim_key(self, claim_name: str) -> str:
        return f"claim:{claim_name}"
    
    async def create(self, session: Session) -> Session:
        r = await self._get_redis()
        key = self._session_key(session.id)
        data = json.dumps(asdict(session), default=str)
        await r.set(key, data, ex=86400)  # 24h TTL
        return session
    
    async def get(self, session_id: str) -> Optional[Session]:
        r = await self._get_redis()
        key = self._session_key(session_id)
        data = await r.get(key)
        if data:
            session_dict = json.loads(data)
            # Convert status string back to enum
            if 'status' in session_dict and isinstance(session_dict['status'], str):
                session_dict['status'] = SessionStatus(session_dict['status'])
            # Convert task dict back to AgentTask dataclass
            if 'task' in session_dict and session_dict['task'] and isinstance(session_dict['task'], dict):
                session_dict['task'] = AgentTask(**session_dict['task'])
            # Convert datetime strings back to datetime objects
            for field in ['created_at', 'updated_at', 'started_at', 'completed_at']:
                if field in session_dict and session_dict[field] and isinstance(session_dict[field], str):
                    session_dict[field] = datetime.fromisoformat(session_dict[field].replace('Z', '+00:00'))
            return Session(**session_dict)
        return None
    
    async def update(self, session: Session) -> Session:
        r = await self._get_redis()
        key = self._session_key(session.id)
        session.updated_at = datetime.utcnow()
        data = json.dumps(asdict(session), default=str)
        await r.set(key, data, ex=86400)
        return session
    
    async def delete(self, session_id: str) -> bool:
        r = await self._get_redis()
        key = self._session_key(session_id)
        result = await r.delete(key)
        return result > 0
    
    async def list(self, status: Optional[SessionStatus] = None) -> List[Session]:
        r = await self._get_redis()
        sessions = []
        async for key in r.scan_iter("session:*"):
            data = await r.get(key)
            if data:
                session_dict = json.loads(data)
                # Convert status string back to enum
                if 'status' in session_dict and isinstance(session_dict['status'], str):
                    session_dict['status'] = SessionStatus(session_dict['status'])
                session = Session(**session_dict)
                if status is None or session.status == status:
                    sessions.append(session)
        return sessions
    
    async def close(self):
        if self._redis:
            await self._redis.close()
            self._redis = None


class PostgresSessionStore:
    """PostgreSQL-backed session store for persistence and queries."""
    
    def __init__(self, postgres_url: str):
        self.engine = create_async_engine(postgres_url, echo=False, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
    
    async def initialize(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    async def create(self, session: Session) -> Session:
        async with self.session_factory() as db:
            model = SessionModel(
                id=session.id,
                task_name=session.task.name if session.task else "",
                task_description=session.task.description if session.task else "",
                repo_url=session.task.repo_url if session.task else "",
                repo_branch=session.task.repo_branch if session.task else "main",
                commands=session.task.commands if session.task else [],
                env_vars=session.task.env_vars if session.task else {},
                timeout_seconds=session.task.timeout_seconds if session.task else 600,
                auto_release=session.task.auto_release if session.task else False,
                status=session.status.value,
                sandbox_name=session.sandbox_name,
                claim_name=session.claim_name,
                created_at=session.created_at,
                started_at=session.started_at,
                completed_at=session.completed_at,
                result=session.result,
                error=session.error,
                checkpoint_data=session.checkpoint_data,
            )
            db.add(model)
            await db.commit()
        return session
    
    async def get(self, session_id: str) -> Optional[Session]:
        async with self.session_factory() as db:
            result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
            model = result.scalar_one_or_none()
            if not model:
                return None
            return self._model_to_session(model)
    
    async def update(self, session: Session) -> Session:
        async with self.session_factory() as db:
            result = await db.execute(select(SessionModel).where(SessionModel.id == session.id))
            model = result.scalar_one_or_none()
            if model:
                model.status = session.status.value
                model.sandbox_name = session.sandbox_name
                model.claim_name = session.claim_name
                model.started_at = session.started_at
                model.completed_at = session.completed_at
                model.result = session.result
                model.error = session.error
                model.checkpoint_data = session.checkpoint_data
                model.updated_at = datetime.utcnow()
                await db.commit()
        return session
    
    async def delete(self, session_id: str) -> bool:
        async with self.session_factory() as db:
            result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
            model = result.scalar_one_or_none()
            if model:
                await db.delete(model)
                await db.commit()
                return True
        return False
    
    async def list(self, status: Optional[SessionStatus] = None) -> List[Session]:
        async with self.session_factory() as db:
            query = select(SessionModel).order_by(SessionModel.created_at.desc())
            if status:
                query = query.where(SessionModel.status == status.value)
            result = await db.execute(query)
            models = result.scalars().all()
            return [self._model_to_session(m) for m in models]
    
    def _model_to_session(self, model: SessionModel) -> Session:
        task = AgentTask(
            id=model.id,
            name=model.task_name,
            description=model.task_description,
            repo_url=model.repo_url,
            repo_branch=model.repo_branch,
            commands=model.commands or [],
            env_vars=model.env_vars or {},
            timeout_seconds=model.timeout_seconds,
            auto_release=model.auto_release,
            created_at=model.created_at,
        )
        session = Session(
            id=model.id,
            task=task,
            status=SessionStatus(model.status),
            sandbox_name=model.sandbox_name,
            claim_name=model.claim_name,
            created_at=model.created_at,
            updated_at=model.updated_at,
            started_at=model.started_at,
            completed_at=model.completed_at,
            result=model.result,
            error=model.error,
            checkpoint_data=model.checkpoint_data,
        )
        return session
    
    async def close(self):
        await self.engine.dispose()


class HybridSessionStore:
    """Combines Redis (fast reads) with PostgreSQL (persistence)."""
    
    def __init__(self, redis_url: str, postgres_url: str):
        self.redis = RedisSessionStore(redis_url)
        self.postgres = PostgresSessionStore(postgres_url)
        self._initialized = False
    
    async def initialize(self):
        await self.postgres.initialize()
        self._initialized = True
    
    async def create(self, session: Session) -> Session:
        await self.postgres.create(session)
        await self.redis.create(session)
        return session
    
    async def get(self, session_id: str) -> Optional[Session]:
        # Try Redis first
        session = await self.redis.get(session_id)
        if session:
            return session
        # Fallback to PostgreSQL
        session = await self.postgres.get(session_id)
        if session:
            await self.redis.create(session)  # Cache it
        return session
    
    async def update(self, session: Session) -> Session:
        await self.postgres.update(session)
        await self.redis.update(session)
        return session
    
    async def delete(self, session_id: str) -> bool:
        await self.postgres.delete(session_id)
        await self.redis.delete(session_id)
        return True
    
    async def list(self, status: Optional[SessionStatus] = None) -> List[Session]:
        return await self.postgres.list(status)
    
    async def close(self):
        await self.redis.close()
        await self.postgres.close()


class SandboxManager:
    """Manages sandbox lifecycle via agent-sandbox CRDs."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._init_k8s_client()

    def _init_k8s_client(self):
        if self.settings.kubeconfig_path:
            config.load_kube_config(config_file=self.settings.kubeconfig_path)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()

        self.custom_api = client.CustomObjectsApi()
        self.core_api = client.CoreV1Api()
        self.group = "extensions.agents.x-k8s.io"
        self.version = "v1beta1"
        self.namespace = self.settings.namespace

    async def claim_sandbox(self, session_id: str, ttl_seconds: Optional[int] = None) -> tuple[str, str]:
        """Claim a sandbox from the warm pool. Returns (claim_name, sandbox_name)."""
        claim_name = f"claim-{session_id}"
        ttl = ttl_seconds or self.settings.default_ttl_seconds

        claim_body = {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "SandboxClaim",
            "metadata": {
                "name": claim_name,
                "namespace": self.namespace,
                "labels": {
                    "session-id": session_id,
                    "managed-by": "orchestrator"
                }
            },
            "spec": {
                "warmPoolRef": {
                    "name": self.settings.warm_pool_name
                },
                "sandboxName": f"sandbox-{session_id}",
                "lifecycle": {
                    "ttlSecondsAfterFinished": ttl
                }
            }
        }

        try:
            logger.info(f"Creating SandboxClaim: {claim_name}")
            await asyncio.to_thread(
                self.custom_api.create_namespaced_custom_object,
                self.group, self.version, self.namespace,
                "sandboxclaims", claim_body
            )

            # Wait for claim to bind
            sandbox_name = await self._wait_for_claim_bound(claim_name)
            logger.info(f"Claim {claim_name} bound to sandbox: {sandbox_name}")
            return claim_name, sandbox_name

        except ApiException as e:
            logger.error(f"Failed to create claim: {e}")
            raise

    async def _wait_for_claim_bound(self, claim_name: str, timeout: int = 60) -> str:
        """Wait for claim to be bound to a sandbox."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            try:
                claim = await asyncio.to_thread(
                    self.custom_api.get_namespaced_custom_object,
                    self.group, self.version, self.namespace,
                    "sandboxclaims", claim_name
                )
                status = claim.get("status", {})
                # Check for Ready condition
                conditions = status.get("conditions", [])
                for cond in conditions:
                    if cond.get("type") == "Ready" and cond.get("status") == "True":
                        sandbox_name = status.get("sandbox", {}).get("name")
                        if sandbox_name:
                            return sandbox_name
                # Also check legacy phase field
                if status.get("phase") == "Bound" and status.get("sandboxName"):
                    return status["sandboxName"]
                if status.get("phase") == "Failed":
                    raise RuntimeError(f"Claim {claim_name} failed: {status.get('message', 'Unknown error')}")
            except ApiException as e:
                if e.status == 404:
                    await asyncio.sleep(1)
                    continue
                raise
            await asyncio.sleep(1)
        raise TimeoutError(f"Claim {claim_name} not bound within {timeout}s")

    async def release_sandbox(self, claim_name: str, sandbox_name: str) -> bool:
        """Release sandbox back to pool or delete it."""
        try:
            # Delete the claim - this triggers release per reclaimPolicy
            await asyncio.to_thread(
                self.custom_api.delete_namespaced_custom_object,
                self.group, self.version, self.namespace,
                "sandboxclaims", claim_name
            )
            logger.info(f"Released claim {claim_name} and sandbox {sandbox_name}")
            return True
        except ApiException as e:
            if e.status == 404:
                return True
            logger.error(f"Failed to release claim {claim_name}: {e}")
            return False

    async def get_sandbox_status(self, sandbox_name: str) -> Optional[Dict]:
        """Get sandbox status."""
        try:
            sandbox = await asyncio.to_thread(
                self.custom_api.get_namespaced_custom_object,
                "agents.x-k8s.io", "v1beta1", self.namespace,
                "sandboxes", sandbox_name
            )
            return sandbox.get("status", {})
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    async def get_pod_name(self, sandbox_name: str) -> str:
        """Resolve pod name for a sandbox."""
        try:
            pod = await asyncio.to_thread(
                self.core_api.read_namespaced_pod,
                sandbox_name, self.namespace
            )
            return pod.metadata.name
        except ApiException as e:
            if e.status != 404:
                raise

        status = await self.get_sandbox_status(sandbox_name) or {}
        selector = status.get("selector")
        if selector:
            pods = await asyncio.to_thread(
                self.core_api.list_namespaced_pod,
                self.namespace, label_selector=selector
            )
            if pods.items:
                return pods.items[0].metadata.name

        return sandbox_name

    async def exec_in_sandbox(
        self,
        sandbox_name: str,
        command: List[str] | str,
        workdir: str = "/workspace",
        timeout: int = 300,
        env_vars: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Execute a command in the sandbox via kubectl exec."""
        pod_name = await self.get_pod_name(sandbox_name)

        if isinstance(command, list):
            if len(command) >= 3 and command[0] == "bash" and command[1] == "-c":
                cmd_to_run = command[2]
            else:
                import shlex
                cmd_to_run = shlex.join(command)
        else:
            cmd_to_run = str(command)

        env_prefix = ""
        if env_vars:
            import shlex
            env_prefix = " ".join([f"{k}={shlex.quote(str(v))}" for k, v in env_vars.items()]) + " "

        bash_script = f"cd '{workdir}' && {env_prefix}{cmd_to_run}"
        full_cmd = ["kubectl", "exec", "-n", self.namespace, pod_name, "--", "bash", "-c", bash_script]

        logger.info(f"Executing in sandbox {sandbox_name} (pod: {pod_name}): {bash_script[:120]}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
                "stderr": stderr.decode("utf-8", errors="replace") if stderr else ""
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s"}
        except Exception as e:
            logger.error(f"Exec failed: {e}")
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}


class SessionStore:
    """In-memory session store (replace with Redis/DB in production)."""

    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def create(self, session: Session) -> Session:
        async with self._lock:
            self._sessions[session.id] = session
            return session

    async def get(self, session_id: str) -> Optional[Session]:
        async with self._lock:
            return self._sessions.get(session_id)

    async def update(self, session: Session) -> Session:
        async with self._lock:
            session.updated_at = datetime.utcnow()
            self._sessions[session.id] = session
            return session

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    async def list(self, status: Optional[SessionStatus] = None) -> List[Session]:
        async with self._lock:
            sessions = list(self._sessions.values())
            if status:
                sessions = [s for s in sessions if s.status == status]
            return sessions


class Orchestrator:
    """Main orchestrator that coordinates sessions, sandboxes, and task execution."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.sandbox_mgr = SandboxManager(settings)
        self.store = HybridSessionStore(settings.redis_url, settings.postgres_url)
        self._running_tasks: Dict[str, asyncio.Task] = {}

    async def initialize(self):
        """Initialize the session store."""
        await self.store.initialize()

    async def start_session(self, task: AgentTask) -> Session:
        """Start a new agent session for a task."""
        session = Session(task=task)
        await self.store.create(session)

        # Start the session workflow in background
        task_obj = asyncio.create_task(self._run_session_workflow(session.id))
        self._running_tasks[session.id] = task_obj

        return session

    async def _run_session_workflow(self, session_id: str):
        """Main session workflow: claim sandbox -> wait for ready -> execute task."""
        session = await self.store.get(session_id)
        if not session:
            return

        try:
            # Phase 1: Claim sandbox
            await self._update_session_status(session, SessionStatus.CLAIMING_SANDBOX)
            timeout = (session.task.timeout_seconds if session.task else 600) + 60
            claim_name, sandbox_name = await self.sandbox_mgr.claim_sandbox(
                session_id, timeout
            )
            session.claim_name = claim_name
            session.sandbox_name = sandbox_name
            await self.store.update(session)

            # Phase 2: Wait for sandbox ready
            await self._wait_for_sandbox_ready(sandbox_name)

            # Phase 3: Sandbox ready - mark as RUNNING
            await self._update_session_status(session, SessionStatus.RUNNING)
            session.started_at = datetime.utcnow()
            await self.store.update(session)

            # Phase 4: Execute task commands if provided
            if session.task and session.task.commands:
                result = await self._execute_task(session)
                session.result = result
                session.completed_at = datetime.utcnow()
                if result.get("success"):
                    await self._update_session_status(session, SessionStatus.COMPLETED)
                else:
                    error_msg = f"Command failed at step {result.get('failed_at_command')}"
                    await self._update_session_status(session, SessionStatus.FAILED, error_msg)

                # If auto_release is requested, exit so finally block releases it
                if getattr(session.task, "auto_release", False):
                    return

            # Keep session alive until explicitly cancelled
            while True:
                await asyncio.sleep(5)
                status = await self.sandbox_mgr.get_sandbox_status(sandbox_name)
                if not status:
                    raise RuntimeError(f"Sandbox {sandbox_name} was deleted")

        except asyncio.CancelledError:
            await self._update_session_status(session, SessionStatus.INTERRUPTED)
            raise
        except Exception as e:
            logger.exception(f"Session {session_id} failed")
            await self._update_session_status(session, SessionStatus.FAILED, str(e))
        finally:
            if getattr(session.task, "auto_release", False) or session.status in (SessionStatus.FAILED, SessionStatus.INTERRUPTED):
                if session.claim_name and session.sandbox_name:
                    await self.sandbox_mgr.release_sandbox(session.claim_name, session.sandbox_name)
            self._running_tasks.pop(session_id, None)

    async def _update_session_status(self, session: Session, status: SessionStatus, error: str = None):
        session.status = status
        if error:
            session.error = error
        await self.store.update(session)

    async def _wait_for_sandbox_ready(self, sandbox_name: str, timeout: int = 120):
        """Wait for sandbox pod to be running."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            status = await self.sandbox_mgr.get_sandbox_status(sandbox_name)
            if status:
                conditions = status.get("conditions", [])
                for cond in conditions:
                    if cond.get("type") == "Ready" and cond.get("status") == "True":
                        return
            await asyncio.sleep(2)
        raise TimeoutError(f"Sandbox {sandbox_name} not ready within {timeout}s")

    async def _execute_task(self, session: Session) -> Dict[str, Any]:
        """Execute the task commands in the sandbox."""
        task = session.task
        sandbox_name = session.sandbox_name

        results = {
            "task_id": task.id if task else "",
            "commands": [],
            "started_at": datetime.utcnow().isoformat(),
        }

        # Prepare workspace: clone repo if provided
        if task and task.repo_url:
            clone_cmd = ["git", "clone", "--depth", "1", "--branch", task.repo_branch, task.repo_url, "/workspace/repo"]
            clone_res = await self._run_command(session, clone_cmd, "clone repo")
            if clone_res.get("exit_code", -1) != 0:
                results["failed_at_command"] = -1
                results["error"] = f"Failed to clone repo: {clone_res.get('stderr')}"
                results["completed_at"] = datetime.utcnow().isoformat()
                results["success"] = False
                return results
            workdir = "/workspace/repo"
        else:
            workdir = "/workspace"

        # Run each command
        if task and task.commands:
            for i, cmd_str in enumerate(task.commands):
                cmd_result = await self._run_command(session, cmd_str, f"command_{i}", workdir=workdir)
                results["commands"].append({
                    "command": cmd_str,
                    "exit_code": cmd_result.get("exit_code", -1),
                    "stdout": cmd_result.get("stdout", "")[-5000:],
                    "stderr": cmd_result.get("stderr", "")[-2000:],
                })

                if cmd_result.get("exit_code", -1) != 0:
                    results["failed_at_command"] = i
                    break

        results["completed_at"] = datetime.utcnow().isoformat()
        results["success"] = results.get("failed_at_command") is None
        return results

    async def _run_command(self, session: Session, command: List[str] | str, label: str, workdir: str = "/workspace", timeout: int = 300) -> Dict[str, Any]:
        """Run a command in the sandbox."""
        sandbox_name = session.sandbox_name
        if not sandbox_name:
            raise RuntimeError("No sandbox assigned")

        env_vars = session.task.env_vars if session.task else None
        logger.info(f"[{session.id}] Running: {label}")
        return await self.sandbox_mgr.exec_in_sandbox(
            sandbox_name=sandbox_name,
            command=command,
            workdir=workdir,
            timeout=timeout,
            env_vars=env_vars
        )

    async def _save_checkpoint(self, session: Session, result: Dict):
        """Save session checkpoint (workspace + conversation state)."""
        checkpoint = {
            "session_id": session.id,
            "task": asdict(session.task) if session.task else None,
            "result": result,
            "timestamp": datetime.utcnow().isoformat(),
            "workspace_snapshot": f"s3://bucket/checkpoints/{session.id}.tar.gz"  # Placeholder
        }
        session.checkpoint_data = checkpoint
        logger.info(f"Checkpoint saved for session {session.id}")

    async def get_session(self, session_id: str) -> Optional[Session]:
        return await self.store.get(session_id)

    async def cancel_session(self, session_id: str) -> bool:
        session = await self.store.get(session_id)
        if not session:
            return False
        task = self._running_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if session.claim_name and session.sandbox_name:
            await self.sandbox_mgr.release_sandbox(session.claim_name, session.sandbox_name)
            session.claim_name = None
            session.sandbox_name = None
            await self.store.update(session)
        return True


# FastAPI app for REST API
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

settings = Settings()

# Initialize Redis and SQLAlchemy instrumentation
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    orchestrator = Orchestrator(settings)
    await orchestrator.initialize()
    app.state.orchestrator = orchestrator
    logger.info("Orchestrator initialized")
    yield
    # Shutdown
    await orchestrator.store.close()
    logger.info("Orchestrator shutdown")

app = FastAPI(title="Agent Sandbox Orchestrator", version="0.1.0", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


class TaskRequest(BaseModel):
    name: str
    description: str = ""
    repo_url: str = ""
    repo_branch: str = "main"
    commands: List[str] = Field(default_factory=list)
    env_vars: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = 600
    auto_release: bool = False


class TaskResponse(BaseModel):
    session_id: str
    status: str
    message: str


@app.get("/sessions")
async def list_sessions():
    orchestrator = app.state.orchestrator
    sessions = await orchestrator.store.list()
    return [
        {
            "id": s.id,
            "status": s.status.value,
            "task": asdict(s.task) if s.task else None,
            "sandbox_name": s.sandbox_name,
            "created_at": s.created_at.isoformat(),
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "result": s.result,
            "error": s.error,
        }
        for s in sessions
    ]


@app.post("/sessions", response_model=TaskResponse)
async def create_session(request: TaskRequest):
    orchestrator = app.state.orchestrator
    task = AgentTask(
        name=request.name,
        description=request.description,
        repo_url=request.repo_url,
        repo_branch=request.repo_branch,
        commands=request.commands,
        env_vars=request.env_vars,
        timeout_seconds=request.timeout_seconds,
        auto_release=request.auto_release
    )
    session = await orchestrator.start_session(task)
    return TaskResponse(
        session_id=session.id,
        status=session.status.value,
        message="Session started"
    )


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    orchestrator = app.state.orchestrator
    session = await orchestrator.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "id": session.id,
        "status": session.status.value,
        "task": asdict(session.task) if session.task else None,
        "sandbox_name": session.sandbox_name,
        "created_at": session.created_at.isoformat(),
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "result": session.result,
        "error": session.error,
    }


@app.delete("/sessions/{session_id}")
async def cancel_session(session_id: str):
    orchestrator = app.state.orchestrator
    success = await orchestrator.cancel_session(session_id)
    if not success:
        raise HTTPException(404, "Session not found or not running")
    return {"message": "Session cancelled"}


@app.get("/health")
async def health():
    return {"status": "healthy", "orchestrator": "running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.orchestrator_port)