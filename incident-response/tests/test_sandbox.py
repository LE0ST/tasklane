"""Security boundary and sandbox containment unit tests."""
import os
from pathlib import Path
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from sandbox import (
    SecurityViolation,
    create_disposable_workspace,
    cleanup_disposable_workspace,
    run_sandboxed_command,
    sanitize_process_environment,
    validate_safe_workspace_path,
)


def test_safe_workspace_path_within_base(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    child_ws = base_dir / "ws_123"
    child_ws.mkdir()

    validated = validate_safe_workspace_path(child_ws, allowed_base=base_dir)
    assert validated == child_ws.resolve()


def test_safe_workspace_path_rejects_parent_traversal(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    outside_target = tmp_path / "outside"
    outside_target.mkdir()

    traversal_path = base_dir / ".." / "outside"
    with pytest.raises(SecurityViolation) as exc_info:
        validate_safe_workspace_path(traversal_path, allowed_base=base_dir)
    assert "Path escape rejected" in str(exc_info.value)


def test_safe_workspace_path_rejects_identical_to_base(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()

    # The base itself is not a valid child workspace
    # Because target must reside inside base
    ws_child = base_dir / "child"
    ws_child.mkdir()
    assert validate_safe_workspace_path(ws_child, allowed_base=base_dir)


def test_sanitize_process_environment_strips_sensitive_credentials():
    os.environ["GITHUB_TOKEN"] = "ghp_secret12345"
    os.environ["DATABASE_URL"] = "postgresql://user:pass@remote:5432/db"
    os.environ["RENDER_API_KEY"] = "rnd_secret999"
    os.environ["CUSTOM_AUTH_KEY"] = "supersecret"

    try:
        clean = sanitize_process_environment()
        assert "GITHUB_TOKEN" not in clean
        assert "DATABASE_URL" not in clean
        assert "RENDER_API_KEY" not in clean
        assert "CUSTOM_AUTH_KEY" not in clean
        assert clean.get("ENVIRONMENT") == "disposable_test"
    finally:
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RENDER_API_KEY", None)
        os.environ.pop("CUSTOM_AUTH_KEY", None)


def test_forbidden_flags_rejected_by_sandboxed_command(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    # --dangerously-bypass-approvals-and-sandbox must be blocked
    cmd = ["python", "-c", "print('hello')", "--dangerously-bypass-approvals-and-sandbox"]
    with pytest.raises(SecurityViolation) as exc_info:
        run_sandboxed_command(cmd, cwd=ws, allowed_base=tmp_path)
    assert "Forbidden command parameter rejected" in str(exc_info.value)


def test_sandboxed_command_timeout(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    # Sleep longer than timeout (timeout=1 second)
    cmd = ["python", "-c", "import time; time.sleep(3)"]
    with pytest.raises(TimeoutError) as exc_info:
        run_sandboxed_command(cmd, cwd=ws, timeout=1, allowed_base=tmp_path)
    assert "exceeded timeout" in str(exc_info.value)
