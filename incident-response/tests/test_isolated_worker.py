"""Offline security and functional verification for the Isolated Test Worker."""
import os
import shutil
import sys
from pathlib import Path
import pytest

IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from isolated_worker import IsolatedWorkerController
from sandbox import is_isolated_worker_available
from agent_adapter import DeepSeekAgentAdapter, ERROR_BLOCKED_MISSING_ISOLATED_WORKER


def test_isolated_worker_availability_check():
    """Verify that IsolatedWorkerController properly detects local Docker and worker image."""
    available, reason = IsolatedWorkerController.check_availability()
    # In this environment, Docker is running and tasklane-isolated-worker:local is built
    assert available is True
    assert reason == "available"
    assert is_isolated_worker_available() is True


def test_isolated_worker_offline_security_verifications():
    """Verify all 6 offline security boundaries inside the isolated worker container:
    1. Network disabled (no non-loopback egress).
    2. Absence of Docker socket (/var/run/docker.sock).
    3. Absence of .git checkout directory.
    4. Absence of credentials or API keys in environment.
    5. Non-root user identity (uid 10001).
    6. Read-only root filesystem enforcement.
    """
    results = IsolatedWorkerController.verify_isolation_offline()
    assert results.get("network_disabled") is True, f"Network isolation failed: {results}"
    assert results.get("no_docker_socket") is True, f"Docker socket leaked: {results}"
    assert results.get("no_git_checkout") is True, f".git checkout leaked: {results}"
    assert results.get("no_credentials") is True, f"Credentials present in env: {results}"
    assert results.get("non_root_user") is True, f"Non-root execution failed: {results}"
    assert results.get("read_only_root") is True, f"Read-only filesystem failed: {results}"


def test_isolated_worker_staging_preparation_excludes_git_and_env(tmp_path):
    """Verify that prepare_staging_workspace copies ONLY positive manifest files
    and strictly excludes .git, .env, and unrelated host files.
    """
    repo_root = Path(__file__).resolve().parents[2]
    staging_dir = IsolatedWorkerController.prepare_staging_workspace(repo_root, staging_parent=tmp_path)

    try:
        # Positive manifest files must exist
        assert (staging_dir / "backend" / "main.py").exists()
        assert (staging_dir / "backend" / "_verify_tasks.py").exists()

        # Negative checks: sensitive items must NOT exist
        assert not (staging_dir / ".git").exists()
        assert not (staging_dir / ".env").exists()
        assert not (staging_dir / "docker.sock").exists()
        assert not (staging_dir / "incident-response").exists()
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def test_isolated_worker_runs_verification_and_detects_defect(tmp_path):
    """Verify that isolated worker executes candidate code inside the container
    and accurately reports defect presence vs clean recovery.
    """
    repo_root = Path(__file__).resolve().parents[2]

    # 1. Clean code execution
    staging_clean = IsolatedWorkerController.prepare_staging_workspace(repo_root, staging_parent=tmp_path)
    ok_clean, data_clean, out_clean = IsolatedWorkerController.run_isolated_verification(staging_clean)
    assert ok_clean is True
    assert data_clean.get("normal_status") == 201
    assert data_clean.get("urgent_status") == 201
    assert data_clean.get("urgent_persisted") is True

    # 2. Defect injected in candidate code
    staging_buggy = IsolatedWorkerController.prepare_staging_workspace(repo_root, staging_parent=tmp_path)
    buggy_main = staging_buggy / "backend" / "main.py"
    buggy_content = (
        buggy_main.read_text(encoding="utf-8")
        + "\n# Defect injection\n"
        + "if getattr(task_in, 'priority', None) == 'urgent':\n"
        + "    raise RuntimeError('DEFECT_500')\n"
    )
    buggy_main.write_text(buggy_content, encoding="utf-8")

    ok_buggy, data_buggy, out_buggy = IsolatedWorkerController.run_isolated_verification(staging_buggy)
    assert ok_buggy is False


def test_repair_fails_closed_when_isolated_worker_unavailable(monkeypatch, tmp_path):
    """Verify that if worker runtime is unavailable, repair immediately fails closed
    with blocked_missing_isolated_worker without executing any code.
    """
    monkeypatch.setattr(IsolatedWorkerController, "is_available", classmethod(lambda cls: False))
    monkeypatch.setattr("sandbox.is_isolated_worker_available", lambda: False)
    monkeypatch.setattr("agent_adapter.is_isolated_worker_available", lambda: False)

    adapter = DeepSeekAgentAdapter()
    adapter.write_capable_enabled = True

    ws = tmp_path / "ws"
    ws.mkdir()
    res = adapter.repair({}, workspace_path=ws, operator_approved=True)

    assert res.get("status") == ERROR_BLOCKED_MISSING_ISOLATED_WORKER
    assert res.get("model_executed") is False
    assert "no isolated test worker" in res.get("error")


def test_deepseek_adapter_repair_succeeds_using_real_isolated_worker(monkeypatch):
    """Verify end-to-end remediation pipeline using the real isolated worker:
    1. Model response is mocked (offline; no real API key or network).
    2. Model call happens OUTSIDE the worker on the trusted host.
    3. Candidate code is evaluated INSIDE the isolated container without network.
    4. Successful verification applies the fix atomically to the workspace.
    """
    import difflib
    import json
    from unittest.mock import MagicMock, patch
    from fixtures import (
        BUG_FIXTURE_MARKER_END,
        BUG_FIXTURE_MARKER_START,
        get_target_file,
        inject_task_creation_defect,
    )
    from sandbox import cleanup_disposable_workspace, create_disposable_workspace

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-isolated-repair-success")

    try:
        inject_task_creation_defect(ws)
        target_file = get_target_file(ws)
        buggy_content = target_file.read_text(encoding="utf-8")

        in_bug_block = False
        clean_lines = []
        for l in buggy_content.splitlines(keepends=True):
            if BUG_FIXTURE_MARKER_START in l:
                in_bug_block = True
                continue
            if BUG_FIXTURE_MARKER_END in l:
                in_bug_block = False
                continue
            if not in_bug_block:
                clean_lines.append(l)

        valid_diff = "".join(
            difflib.unified_diff(
                buggy_content.splitlines(keepends=True),
                clean_lines,
                fromfile="a/backend/main.py",
                tofile="b/backend/main.py",
            )
        )

        adapter = DeepSeekAgentAdapter()
        adapter.write_capable_enabled = True

        mock_payload = {
            "choices": [{"message": {"role": "assistant", "content": f"```diff\n{valid_diff}\n```"}}]
        }
        body = json.dumps(mock_payload).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.OpenerDirector.open", return_value=mock_resp):
            res = adapter.repair(
                {"alertname": "TaskLane5xxRate", "trace_id": "test-trace"},
                workspace_path=ws,
                operator_approved=True,
            )

        assert res.get("status") == "repaired"
        assert res.get("model_executed") is True
        assert res.get("tests_passed") is True
        assert res.get("http_verified") is True
        assert res.get("remediation_type") == "model_unified_diff"

        # Confirm target file was actually patched
        repaired_file_content = target_file.read_text(encoding="utf-8")
        assert BUG_FIXTURE_MARKER_START not in repaired_file_content
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-isolated-repair-success")


def test_deepseek_adapter_repair_preserves_target_file_when_worker_fails(monkeypatch):
    """Verify that if candidate code fails verification inside the isolated worker,
    the workspace target file is NEVER modified with the broken code.
    """
    import json
    from unittest.mock import MagicMock, patch
    from fixtures import get_target_file, inject_task_creation_defect
    from sandbox import cleanup_disposable_workspace, create_disposable_workspace

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-isolated-repair-failure")

    try:
        inject_task_creation_defect(ws)
        target_file = get_target_file(ws)
        original_content = target_file.read_text(encoding="utf-8")

        # Synthetic diff that introduces a runtime error
        broken_diff = (
            "--- a/backend/main.py\n"
            "+++ b/backend/main.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-import logging\n"
            "+import logging; raise RuntimeError('WORKER_SIMULATED_TEST_FAILURE')\n"
            " from contextlib import asynccontextmanager\n"
            " from datetime import datetime, timezone\n"
        )

        adapter = DeepSeekAgentAdapter()
        adapter.write_capable_enabled = True

        mock_payload = {
            "choices": [{"message": {"role": "assistant", "content": broken_diff}}]
        }
        body = json.dumps(mock_payload).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.OpenerDirector.open", return_value=mock_resp):
            res = adapter.repair(
                {"alertname": "TaskLane5xxRate", "trace_id": "test-trace"},
                workspace_path=ws,
                operator_approved=True,
            )

        assert res.get("status") == "repair_failed"
        assert res.get("tests_passed") is False
        assert res.get("http_verified") is False

        # Confirm workspace target file was preserved exactly as before
        current_file_content = target_file.read_text(encoding="utf-8")
        assert current_file_content == original_content
        assert "WORKER_SIMULATED_TEST_FAILURE" not in current_file_content
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-isolated-repair-failure")
