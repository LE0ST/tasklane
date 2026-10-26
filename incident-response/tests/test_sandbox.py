"""Security boundary and sandbox containment unit tests."""
import os
from pathlib import Path
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from config import (
    DISPOSABLE_SERVICE_DIR,
    DISPOSABLE_WORKSPACE_MANIFEST,
    MAX_WORKSPACES_RETAINED,
)
from sandbox import (
    SecurityViolation,
    cleanup_disposable_workspace,
    create_disposable_workspace,
    get_registered_workspace,
    prune_stale_resources,
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


def test_create_disposable_workspace_copies_only_positive_manifest():
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="manifest-test-inc")
    try:
        # Verify explicitly included files exist
        assert (ws / "backend" / "main.py").exists()
        assert (ws / "backend" / "tests" / "test_api.py").exists()

        # Verify excluded items are STRICTLY ABSENT
        assert not (ws / "backend" / "tests" / "conftest.py").exists()
        assert not (ws / "backend" / ".env").exists()
        assert not (ws / "backend" / ".venv").exists()
        assert not (ws / "backend" / "tests" / "test_postgres_integration.py").exists()
        assert not (ws / "backend" / "tests" / "test_telemetry.py").exists()

        # Count total copied files in workspace - must match DISPOSABLE_WORKSPACE_MANIFEST exactly
        copied_files = [p for p in ws.rglob("*") if p.is_file() and not p.name.startswith("workspace_")]
        assert len(copied_files) <= len(DISPOSABLE_WORKSPACE_MANIFEST)
    finally:
        cleanup_disposable_workspace(ws, incident_id="manifest-test-inc")


def test_sanitize_process_environment_rejects_unsafe_overrides():
    unsafe_keys = [
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTEST_ADDOPTS",
        "PYTHONSTARTUP",
        "PYTHONPATH",
        "HTTP_PROXY",
    ]
    for key in unsafe_keys:
        with pytest.raises(SecurityViolation) as exc_info:
            sanitize_process_environment(extra_env={key: "malicious_injection"})
        assert "Unsafe environment override rejected" in str(exc_info.value)


def test_prune_stale_resources_with_100_synthetic_entries(tmp_path):
    """Test scale retention: prune exactly down to MAX_WORKSPACES_RETAINED from 100 entries,
    ensuring directory, path link, token file, and registry entries are all purged.
    """
    import shutil
    created_dirs = []
    created_tokens = []
    created_links = []

    try:
        # Create 100 synthetic resource entries
        for i in range(100):
            inc_id = f"scale_{i:03d}"
            ws_dir = DISPOSABLE_SERVICE_DIR / f"workspace_{inc_id}_fake"
            ws_dir.mkdir(parents=True, exist_ok=True)
            register_workspace(inc_id, ws_dir)

            token_file = DISPOSABLE_SERVICE_DIR / f"auth_{inc_id}.token"
            token_file.write_text("fake_token", encoding="utf-8")

            path_link = DISPOSABLE_SERVICE_DIR / f"workspace_{inc_id}.path"

            created_dirs.append(ws_dir)
            created_tokens.append(token_file)
            created_links.append(path_link)

        # Execute pruning
        prune_stale_resources(max_keep=MAX_WORKSPACES_RETAINED)

        # 1. Workspace directories must be capped at MAX_WORKSPACES_RETAINED
        remaining_ws = list(DISPOSABLE_SERVICE_DIR.glob("workspace_scale_*"))
        assert len([p for p in remaining_ws if p.is_dir()]) <= MAX_WORKSPACES_RETAINED

        # 2. Path links must be capped at MAX_WORKSPACES_RETAINED
        remaining_links = list(DISPOSABLE_SERVICE_DIR.glob("workspace_scale_*.path"))
        assert len(remaining_links) <= MAX_WORKSPACES_RETAINED

        # 3. Tokens must be capped at MAX_WORKSPACES_RETAINED
        remaining_tokens = list(DISPOSABLE_SERVICE_DIR.glob("auth_scale_*.token"))
        assert len(remaining_tokens) <= MAX_WORKSPACES_RETAINED

        # 4. In-memory registry must not have stale entries for deleted workspaces
        from sandbox import _workspace_registry
        for inc_id, path in list(_workspace_registry.items()):
            if inc_id.startswith("scale_"):
                assert path.exists(), f"Stale in-memory registry entry found for deleted {inc_id}"

    finally:
        for ws in created_dirs:
            if ws.exists():
                shutil.rmtree(ws, ignore_errors=True)
        for tf in created_tokens:
            if tf.exists():
                try:
                    tf.unlink()
                except OSError:
                    pass
        for pl in created_links:
            if pl.exists():
                try:
                    pl.unlink()
                except OSError:
                    pass


def test_create_disposable_workspace_enforces_cap_after_creation():
    """Verify that create_disposable_workspace leaves at most MAX_WORKSPACES_RETAINED on disk."""
    repo_root = Path(__file__).resolve().parents[2]
    created = []

    try:
        # Create 5 workspaces sequentially
        for i in range(5):
            ws = create_disposable_workspace(repo_root, incident_id=f"cap_test_{i}")
            created.append(ws)

        # Total workspaces on disk matching pattern must NOT exceed MAX_WORKSPACES_RETAINED
        active_ws = list(DISPOSABLE_SERVICE_DIR.glob("workspace_cap_test_*"))
        assert len([p for p in active_ws if p.is_dir()]) <= MAX_WORKSPACES_RETAINED
    finally:
        for ws in created:
            if ws.exists():
                import shutil
                shutil.rmtree(ws, ignore_errors=True)


