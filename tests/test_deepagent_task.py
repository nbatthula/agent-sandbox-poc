#!/usr/bin/env python3
"""
Test a real DeepAgents-style research task in the sandbox.
This mimics what our DeepAgents service does but runs inside the sandbox.
"""

import asyncio
import sys
import os
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "orchestrator"))

from main import Orchestrator, AgentTask, Settings


async def run_research_task():
    """Run a research task similar to our DeepAgents test."""
    settings = Settings()
    orchestrator = Orchestrator(settings)

    # A realistic research task
    task = AgentTask(
        name="deep-research-test",
        description="Research task: analyze a codebase and produce a summary",
        commands=[
            # Set up workspace
            "cd /workspace",
            "echo '=== Starting Research Task ==='",
            
            # Create a sample codebase to analyze
            "mkdir -p /workspace/sample-project/src",
            "cat > /workspace/sample-project/src/main.py << 'EOF'\n\"\"\"Sample Python project for testing.\"\"\"\n\nimport os\nimport json\nfrom typing import List, Dict\nfrom dataclasses import dataclass\n\n\n@dataclass\nclass User:\n    id: int\n    name: str\n    email: str\n\n\nclass UserService:\n    \"\"\"Service for managing users.\"\"\"\n    \n    def __init__(self):\n        self.users: List[User] = []\n    \n    def add_user(self, name: str, email: str) -> User:\n        user = User(id=len(self.users) + 1, name=name, email=email)\n        self.users.append(user)\n        return user\n    \n    def get_user(self, user_id: int) -> User:\n        for user in self.users:\n            if user.id == user_id:\n                return user\n        return None\n    \n    def list_users(self) -> List[User]:\n        return self.users\n    \n    def to_json(self) -> str:\n        return json.dumps([\n            {\"id\": u.id, \"name\": u.name, \"email\": u.email}\n            for u in self.users\n        ])\n\n\ndef main():\n    service = UserService()\n    service.add_user(\"Alice\", \"alice@example.com\")\n    service.add_user(\"Bob\", \"bob@example.com\")\n    print(service.to_json())\n\n\nif __name__ == \"__main__\":\n    main()\nEOF",
            
            # Analyze the codebase
            "echo '=== Codebase Analysis ==='",
            "find /workspace/sample-project -type f -name '*.py' | head -20",
            "wc -l /workspace/sample-project/src/main.py",
            "grep -n 'class\\|def' /workspace/sample-project/src/main.py",
            
            # Run the code to verify it works
            "echo '=== Running Code ==='",
            "cd /workspace/sample-project && python3 src/main.py",
            
            # Produce research summary
            "echo '=== Research Summary ==='",
            "cat > /workspace/research_summary.md << 'EOF'\n# Research Summary: Sample Project Analysis\n\n## Project Structure\n- Language: Python 3\n- Main module: `src/main.py`\n- Lines of code: ~50\n\n## Key Components\n1. **User dataclass** - Simple data container with id, name, email\n2. **UserService class** - Manages user collection with CRUD operations\n3. **Main function** - Demo usage creating two users\n\n## Code Quality Observations\n- ✓ Type hints used throughout\n- ✓ Dataclass for clean data model\n- ✓ Clear method names and docstrings\n- ⚠ No error handling for duplicate emails\n- ⚠ No persistence layer\n\n## Recommendations\n1. Add email uniqueness validation\n2. Add persistence (SQLite/PostgreSQL)\n3. Add unit tests\n4. Consider using Pydantic for validation\nEOF",
            
            "cat /workspace/research_summary.md",
            "echo '=== Task Complete ==='",
        ],
        timeout_seconds=180
    )

    print("Starting DeepAgents-style research task...")
    session = await orchestrator.start_session(task)
    print(f"✓ Session created: {session.id}")

    # Poll for completion
    max_wait = 240
    start = time.time()
    while time.time() - start < max_wait:
        session = await orchestrator.get_session(session.id)
        if not session:
            print("✗ Session disappeared")
            return False

        print(f"  Status: {session.status.value}", end="\r")

        if session.status == SessionStatus.COMPLETED:
            print(f"\n✓ Research task completed!")
            result = session.result
            print(f"  Success: {result.get('success')}")
            print(f"  Commands run: {len(result.get('commands', []))}")
            
            # Print command outputs
            for i, cmd in enumerate(result.get('commands', [])):
                print(f"\n  Command {i}: {cmd['command'][:60]}...")
                if cmd['stdout']:
                    print(f"    stdout: {cmd['stdout'][:200]}")
                if cmd['stderr']:
                    print(f"    stderr: {cmd['stderr'][:200]}")
            
            await orchestrator.cancel_session(session.id)
            return True

        if session.status == SessionStatus.FAILED:
            print(f"\n✗ Session failed: {session.error}")
            return False

        if session.status == SessionStatus.INTERRUPTED:
            print(f"\n✗ Session interrupted")
            return False

        await asyncio.sleep(5)

    print(f"\n✗ Session timed out after {max_wait}s")
    return False


if __name__ == "__main__":
    import time
    from main import SessionStatus
    success = asyncio.run(run_research_task())
    sys.exit(0 if success else 1)