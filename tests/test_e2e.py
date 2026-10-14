#!/usr/bin/env python3
"""
End-to-end test for agent sandbox orchestrator.
Tests the full flow: create session -> claim sandbox -> run task -> checkpoint -> release.
"""

import asyncio
import sys
import os
import json
import time
from pathlib import Path

# Add orchestrator to path
sys.path.insert(0, str(Path(__file__).parent.parent / "orchestrator"))

from main import (
    Orchestrator, AgentTask, SessionStatus, Settings,
    SandboxManager, SessionStore, Session
)


async def test_sandbox_manager():
    """Test sandbox manager operations."""
    print("\n=== Testing SandboxManager ===")
    settings = Settings()
    mgr = SandboxManager(settings)

    # Test 1: Check warm pool exists
    print("Checking warm pool status...")
    try:
        # We can't easily check warm pool via the custom API without the client
        # Just verify the manager initializes
        print("✓ SandboxManager initialized")
    except Exception as e:
        print(f"✗ SandboxManager init failed: {e}")
        return False

    return True


async def test_session_store():
    """Test session store CRUD operations."""
    print("\n=== Testing SessionStore ===")
    store = SessionStore()

    # Create session
    task = AgentTask(name="test-task", commands=["echo hello"])
    session = await store.create(Session(task=task))
    print(f"✓ Created session: {session.id}")

    # Get session
    retrieved = await store.get(session.id)
    assert retrieved is not None
    assert retrieved.id == session.id
    print("✓ Retrieved session")

    # Update session
    session.status = SessionStatus.RUNNING
    await store.update(session)
    retrieved = await store.get(session.id)
    assert retrieved.status == SessionStatus.RUNNING
    print("✓ Updated session status")

    # List sessions
    sessions = await store.list()
    assert len(sessions) == 1
    print("✓ Listed sessions")

    # Delete session
    await store.delete(session.id)
    retrieved = await store.get(session.id)
    assert retrieved is None
    print("✓ Deleted session")

    return True


async def test_full_orchestration():
    """Test full orchestration flow with a simple task."""
    print("\n=== Testing Full Orchestration ===")
    settings = Settings()
    orchestrator = Orchestrator(settings)

    # Create a simple task
    task = AgentTask(
        name="echo-test",
        description="Simple echo test",
        commands=[
            "echo 'Hello from sandbox'",
            "pwd",
            "ls -la /workspace",
            "python3 -c \"import sys; print(f'Python {sys.version}')\"",
        ],
        timeout_seconds=120
    )

    print(f"Starting session for task: {task.name}")
    session = await orchestrator.start_session(task)
    print(f"✓ Session created: {session.id}")

    # Poll for completion
    max_wait = 180  # seconds
    start = time.time()
    while time.time() - start < max_wait:
        session = await orchestrator.get_session(session.id)
        if not session:
            print("✗ Session disappeared")
            return False

        print(f"  Status: {session.status.value}", end="\r")

        if session.status == SessionStatus.COMPLETED:
            print(f"\n✓ Session completed successfully!")
            print(f"  Result: {json.dumps(session.result, indent=2)}")
            await orchestrator.cancel_session(session.id)
            return True

        if session.status == SessionStatus.FAILED:
            print(f"\n✗ Session failed: {session.error}")
            return False

        if session.status == SessionStatus.INTERRUPTED:
            print(f"\n✗ Session interrupted")
            return False

        await asyncio.sleep(3)

    print(f"\n✗ Session timed out after {max_wait}s")
    return False


async def test_parallel_sessions():
    """Test running multiple sessions in parallel."""
    print("\n=== Testing Parallel Sessions ===")
    settings = Settings(max_concurrent_sessions=3)
    orchestrator = Orchestrator(settings)

    tasks = [
        AgentTask(name=f"parallel-{i}", commands=[f"echo Task {i}; sleep 2; echo Done {i}"])
        for i in range(3)
    ]

    sessions = []
    for task in tasks:
        session = await orchestrator.start_session(task)
        sessions.append(session)
        print(f"✓ Started session {session.id} for {task.name}")

    # Wait for all
    max_wait = 60
    start = time.time()
    completed = 0
    while time.time() - start < max_wait and completed < len(sessions):
        for session in sessions:
            s = await orchestrator.get_session(session.id)
            if s and s.status == SessionStatus.COMPLETED:
                completed += 1
            elif s and s.status == SessionStatus.FAILED:
                print(f"✗ Session {session.id} failed: {s.error}")
                return False
        await asyncio.sleep(2)

    # Cleanup parallel sessions
    for session in sessions:
        await orchestrator.cancel_session(session.id)

    if completed == len(sessions):
        print(f"✓ All {len(sessions)} parallel sessions completed")
        return True
    else:
        print(f"✗ Only {completed}/{len(sessions)} completed in time")
        return False


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Agent Sandbox POC - End-to-End Tests")
    print("=" * 60)

    # Check prerequisites
    print("\nChecking prerequisites...")
    import subprocess
    try:
        result = subprocess.run(["kubectl", "get", "nodes"], capture_output=True, text=True)
        if result.returncode != 0:
            print("✗ kubectl not working")
            return 1
        print("✓ kubectl works")
    except Exception as e:
        print(f"✗ kubectl error: {e}")
        return 1

    # Check agent-sandbox CRDs
    try:
        result = subprocess.run(
            ["kubectl", "get", "crd", "sandboxes.agents.x-k8s.io"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("✗ agent-sandbox CRDs not installed")
            return 1
        print("✓ agent-sandbox CRDs installed")
    except Exception as e:
        print(f"✗ CRD check error: {e}")
        return 1

    # Check warm pool
    try:
        result = subprocess.run(
            ["kubectl", "get", "sandboxwarmpool", "agent-warm-pool", "-n", "default"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("✗ Warm pool not found")
            return 1
        print("✓ Warm pool exists")
    except Exception as e:
        print(f"✗ Warm pool check error: {e}")
        return 1

    # Run tests
    all_passed = True

    all_passed &= await test_session_store()
    all_passed &= await test_sandbox_manager()

    # Only run full orchestration if we want to (it takes longer)
    if os.environ.get("RUN_FULL_TEST", "1") == "1":
        all_passed &= await test_full_orchestration()

    if os.environ.get("RUN_PARALLEL_TEST", "1") == "1":
        all_passed &= await test_parallel_sessions()

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL TESTS PASSED ✓")
        return 0
    else:
        print("SOME TESTS FAILED ✗")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))