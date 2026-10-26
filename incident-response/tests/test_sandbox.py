"""Security boundary and sandbox containment unit tests."""
import os
from pathlib import Path
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from config import DISPOSABLE_SERVICE_DIR
from sandbox import (
    SecurityViolation,
    cleanup_disposable_workspace,
    create_disposable_workspace,
    get_registered_workspace,
    register_workspace,
    reset_workspace_registry,
    run_sandboxed_command,
    sanitize_process_environment,
    validate_safe_workspace_path,
)


@pytest.fixture(autouse=True)
def clean_registry():
    reset_workspace_registry()
    yield
    reset_workspace_registry()


def test_safe_workspace_path_within_registered_service_dir(tmp_path):
    ws_child = DISPOSABLE_SERVICE_DIR / "workspace_test_123"
    ws_child.mkdir(parents=True, exist_ok=True)
    register_workspace("inc-1", ws_child)

    validated = validate_safe_workspace_path(ws_child, required_incident_id="inc-1")
    assert validated == ws_child.resolve()


def test_safe_workspace_path_rejects_parent_traversal():
    outside_target = DISPOSABLE_SERVICE_DIR.parent / "outside_dir"
    outside_target.mkdir(parents=True, exist_ok=True)
    traversal_path = DISPOSABLE_SERVICE_DIR / ".." / "outside_dir"
    try:
        with pytest.raises(SecurityViolation) as exc_info:
            validate_safe_workspace_path(traversal_path, enforce_registry=False)
        assert "Path escape rejected" in str(exc_info.value)
    finally:
        if outside_target.exists():
            try:
                outside_target.rmdir()
            except OSError:
                pass


def test_safe_workspace_path_rejects_disposable_root_itself():
    DISPOSABLE_SERVICE_DIR.mkdir(parents=True, exist_ok=True)
    with pytest.raises(SecurityViolation) as exc_info:
        validate_safe_workspace_path(DISPOSABLE_SERVICE_DIR, enforce_registry=False)
    assert "Target cannot be the disposable directory root" in str(exc_info.value)


def test_safe_workspace_path_rejects_nonexistent_or_file(tmp_path):
    nonexistent = DISPOSABLE_SERVICE_DIR / "does_not_exist"
    with pytest.raises(SecurityViolation) as exc_info:
        validate_safe_workspace_path(nonexistent, enforce_registry=False)
    assert "Target path does not exist" in str(exc_info.value)

    some_file = DISPOSABLE_SERVICE_DIR / "test_file.txt"
    some_file.parent.mkdir(parents=True, exist_ok=True)
    some_file.write_text("hello", encoding="utf-8")
    with pytest.raises(SecurityViolation) as exc_info:
        validate_safe_workspace_path(some_file, enforce_registry=False)
    assert "Target path is not a directory" in str(exc_info.value)


def test_safe_workspace_path_rejects_unregistered_workspace():
    unregistered_ws = DISPOSABLE_SERVICE_DIR / "workspace_unregistered"
    unregistered_ws.mkdir(parents=True, exist_ok=True)

    # enforce_registry=True by default
    with pytest.raises(SecurityViolation) as exc_info:
        validate_safe_workspace_path(unregistered_ws)
    assert "not in the active registered workspace registry" in str(exc_info.value)


def test_safe_workspace_path_rejects_incident_id_mismatch():
    ws_1 = DISPOSABLE_SERVICE_DIR / "ws_1"
    ws_1.mkdir(parents=True, exist_ok=True)
    register_workspace("inc-1", ws_1)

    with pytest.raises(SecurityViolation) as exc_info:
        validate_safe_workspace_path(ws_1, required_incident_id="inc-2")
    assert "is not registered for incident 'inc-2'" in str(exc_info.value)


def test_sanitize_process_environment_minimal_allowlist():
    # Inject sensitive credentials
    os.environ["AWS_ACCESS_KEY_ID"] = "AKIA_FAKE_SECRET_KEY"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "supersecret123"
    os.environ["DATABASE_URL"] = "postgresql://user:pass@remote:5432/proddb"
    os.environ["TEST_DATABASE_URL"] = "postgresql://user:pass@remote:5432/testdb"
    os.environ["GITHUB_TOKEN"] = "ghp_secrettoken999"
    os.environ["HOME"] = "/root/privileged"
    os.environ["PYTHONPATH"] = "/opt/malicious:/opt/hacks"

    try:
        clean = sanitize_process_environment()
        assert "AWS_ACCESS_KEY_ID" not in clean
        assert "AWS_SECRET_ACCESS_KEY" not in clean
        assert "DATABASE_URL" not in clean
        assert "TEST_DATABASE_URL" not in clean
        assert "GITHUB_TOKEN" not in clean
        assert "HOME" not in clean
        assert "PYTHONPATH" not in clean
        assert clean.get("ENVIRONMENT") == "disposable_test"
        assert clean.get("HTTP_PROXY") == "http://127.0.0.1:0"
    finally:
        os.environ.pop("AWS_ACCESS_KEY_ID", None)
        os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("TEST_DATABASE_URL", None)
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("HOME", None)
        os.environ.pop("PYTHONPATH", None)


def test_sanitize_process_environment_rejects_dangerous_extra_env():
    # extra_env with credentials or database urls must be rejected
    with pytest.raises(SecurityViolation) as exc_info:
        sanitize_process_environment(extra_env={"DATABASE_URL": "postgresql://..."})
    assert "Dangerous environment override rejected" in str(exc_info.value)

    with pytest.raises(SecurityViolation):
        sanitize_process_environment(extra_env={"AWS_SECRET_KEY": "..."})

    with pytest.raises(SecurityViolation):
        sanitize_process_environment(extra_env={"MY_SECRET_TOKEN": "..."})


def test_forbidden_flags_rejected_by_sandboxed_command(tmp_path):
    ws = DISPOSABLE_SERVICE_DIR / "ws_forbidden_test"
    ws.mkdir(parents=True, exist_ok=True)
    register_workspace("inc-forbidden", ws)

    cmd = ["python", "-c", "print('hello')", "--dangerously-bypass-approvals-and-sandbox"]
    with pytest.raises(SecurityViolation) as exc_info:
        run_sandboxed_command(cmd, cwd=ws, incident_id="inc-forbidden")
    assert "Forbidden command parameter rejected" in str(exc_info.value)


def test_sandboxed_command_timeout():
    ws = DISPOSABLE_SERVICE_DIR / "ws_timeout_test"
    ws.mkdir(parents=True, exist_ok=True)
    register_workspace("inc-timeout", ws)

    cmd = ["python", "-c", "import time; time.sleep(3)"]
    with pytest.raises(TimeoutError) as exc_info:
        run_sandboxed_command(cmd, cwd=ws, timeout=1, incident_id="inc-timeout")
    assert "exceeded timeout" in str(exc_info.value)
