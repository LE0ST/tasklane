"""Deterministic bug fixture generator for isolated disposable workspace drills.

IMPORTANT: Defect code is NEVER injected into the main repository tree or production image.
It is injected strictly into temporary disposable workspaces for on-call remediation testing.

The defect targets valid urgent-priority task creation in create_task(task_in: TaskCreate, ...):
- Normal tasks (low/medium/high) succeed with HTTP 201.
- Urgent tasks fail with HTTP 500 before repair.
- After repair, urgent tasks succeed with HTTP 201 and are persisted in the local database.
"""
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

BUG_FIXTURE_MARKER_START = "# --- ONCALL-BUG-FIXTURE-START ---"
BUG_FIXTURE_MARKER_END = "# --- ONCALL-BUG-FIXTURE-END ---"

# Narrowly scoped regression: unhandled RuntimeError on urgent task creation
BUG_CODE_BLOCK = f"""{BUG_FIXTURE_MARKER_START}
    if getattr(task_in.priority, "value", task_in.priority) == "urgent":
        raise RuntimeError("UNCAUGHT REGRESSION: Failed to dispatch urgent task")
    {BUG_FIXTURE_MARKER_END}
"""


def get_target_file(workspace_dir: Path) -> Path:
    """Locate the target backend/main.py file within the disposable workspace."""
    target = workspace_dir / "backend" / "main.py"
    if not target.exists():
        target = workspace_dir / "main.py"
    return target


def inject_task_creation_defect(workspace_dir: Path) -> bool:
    """Inject the deterministic task-creation defect into the disposable workspace."""
    target_path = get_target_file(workspace_dir)
    if not target_path.exists():
        raise FileNotFoundError(f"Target file not found in workspace: {target_path}")

    content = target_path.read_text(encoding="utf-8")
    if BUG_FIXTURE_MARKER_START in content:
        return True

    # Find the create_task endpoint definition
    anchor = "def create_task("
    pos = content.find(anchor)
    if pos == -1:
        raise ValueError("Could not find 'def create_task(' in target file")

    colon_pos = content.find("):", pos)
    if colon_pos == -1:
        raise ValueError("Could not locate end of create_task signature")

    docstring_end = colon_pos + 2
    triple_quote_pos = content.find('"""', colon_pos)
    if triple_quote_pos != -1 and triple_quote_pos - colon_pos < 120:
        closing_triple = content.find('"""', triple_quote_pos + 3)
        if closing_triple != -1:
            docstring_end = closing_triple + 3

    new_content = (
        content[:docstring_end]
        + "\n"
        + BUG_CODE_BLOCK
        + content[docstring_end:]
    )
    target_path.write_text(new_content, encoding="utf-8")
    return True


def has_task_creation_defect(workspace_dir: Path) -> bool:
    """Check if the disposable workspace contains the defect."""
    target_path = get_target_file(workspace_dir)
    if not target_path.exists():
        return False
    content = target_path.read_text(encoding="utf-8")
    return BUG_FIXTURE_MARKER_START in content


def remove_task_creation_defect(workspace_dir: Path) -> Tuple[str, bool]:
    """Remove the defect from the disposable workspace and return unified diff."""
    target_path = get_target_file(workspace_dir)
    if not target_path.exists():
        return "", False

    original_content = target_path.read_text(encoding="utf-8")
    if BUG_FIXTURE_MARKER_START not in original_content:
        return "", False

    lines = original_content.splitlines(keepends=True)
    new_lines = []
    in_bug_block = False

    for line in lines:
        if BUG_FIXTURE_MARKER_START in line:
            in_bug_block = True
            continue
        if BUG_FIXTURE_MARKER_END in line:
            in_bug_block = False
            continue
        if not in_bug_block:
            new_lines.append(line)

    repaired_content = "".join(new_lines)

    diff = "".join(
        difflib.unified_diff(
            original_content.splitlines(keepends=True),
            repaired_content.splitlines(keepends=True),
            fromfile=f"a/{target_path.name}",
            tofile=f"b/{target_path.name}",
        )
    )

    target_path.write_text(repaired_content, encoding="utf-8")
    return diff, True


def run_http_task_verification_script(workspace_dir: Path) -> Dict[str, Any]:
    """Execute an isolated Python process that tests actual HTTP task creation
    and local database persistence in the disposable workspace.
    
    Verifies:
    1. Normal task (priority=medium) returns HTTP 201.
    2. Urgent task (priority=urgent) behavior:
       - If bug present: returns HTTP 500
       - If repaired: returns HTTP 201 and persists in DB.
    """
    from sandbox import find_test_python, run_sandboxed_command

    backend_dir = workspace_dir / "backend"
    if not backend_dir.exists():
        backend_dir = workspace_dir

    script_content = """
import sys
import json
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from database import Base, get_db
from main import app
from models import Task

test_engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
Base.metadata.create_all(bind=test_engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app, raise_server_exceptions=False)

# 1. Normal task creation
normal_resp = client.post("/api/tasks", json={"title": "Normal Priority Task", "priority": "medium"})
normal_status = normal_resp.status_code

# 2. Urgent task creation
urgent_resp = client.post("/api/tasks", json={"title": "Urgent Priority Task", "priority": "urgent"})
urgent_status = urgent_resp.status_code

# 3. Persistence verification if urgent succeeded
urgent_persisted = False
if urgent_status == 201:
    task_id = urgent_resp.json().get("id")
    if task_id:
        get_resp = client.get(f"/api/tasks/{task_id}")
        urgent_persisted = (get_resp.status_code == 200 and get_resp.json().get("priority") == "urgent")

result = {
    "normal_status": normal_status,
    "urgent_status": urgent_status,
    "urgent_persisted": urgent_persisted,
}
print("TASKLANE_VERIFY_JSON:" + json.dumps(result))
"""

    verify_script = backend_dir / "_verify_tasks.py"
    verify_script.write_text(script_content, encoding="utf-8")

    try:
        python_bin = find_test_python()
        ret, stdout, stderr = run_sandboxed_command(
            [python_bin, "_verify_tasks.py"],
            cwd=backend_dir,
            timeout=30,
            enforce_registry=False,
        )

        for line in stdout.splitlines():
            if line.startswith("TASKLANE_VERIFY_JSON:"):
                return json.loads(line[len("TASKLANE_VERIFY_JSON:"):])

        return {
            "error": "Failed to parse verification output",
            "normal_status": None,
            "urgent_status": None,
            "urgent_persisted": False,
        }
    finally:
        if verify_script.exists():
            try:
                verify_script.unlink()
            except OSError:
                pass
