"""Offline security and functional verification for the Isolated Test Worker."""
import os
import shutil
import sys
from pathlib import Path
import pytest

IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from isolated_worker import (
    IsolatedWorkerController,
    IsolatedWorkerSession,
    SecurityError,
)
from sandbox import is_isolated_worker_available
from agent_adapter import DeepSeekAgentAdapter, ERROR_BLOCKED_MISSING_ISOLATED_WORKER
from fixtures import inject_task_creation_defect


def test_isolated_worker_availability_check():
    """Verify that IsolatedWorkerController detects local Docker and worker image with immutable ID."""
    available, reason = IsolatedWorkerController.check_availability()
    assert available is True
    assert reason == "available"
    assert is_isolated_worker_available() is True
    image_id = IsolatedWorkerController.get_immutable_image_id()
    assert image_id is not None
    assert image_id.startswith("sha256:")


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
    """Verify that IsolatedWorkerSession copies ONLY positive manifest files
    and strictly excludes .git, .env, and unrelated host files.
    """
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        staging_dir = session.staging_dir
        # Positive manifest files must exist
        assert (staging_dir / "backend" / "main.py").exists()
        assert (staging_dir / "backend" / "_run_functional_harness.py").exists()
        assert (staging_dir / "backend" / "_trusted_verifier.py").exists()

        # Negative checks: sensitive items must NOT exist
        assert not (staging_dir / ".git").exists()
        assert not (staging_dir / ".env").exists()
        assert not (staging_dir / "docker.sock").exists()
        assert not (staging_dir / "incident-response").exists()

    # Centralized cleanup: directory must be deleted after exiting context
    assert not staging_dir.exists()


def test_isolated_worker_staging_rejects_symlinks_and_escapes(tmp_path):
    """Regression test (Audits Sol/Astra): Verify that staging creation strictly rejects
    symlinks, path traversal (..), and escaping paths in manifest files.
    """
    repo_root = Path(__file__).resolve().parents[2]

    # Test 1: path traversal with ..
    with pytest.raises(SecurityError, match="escape"):
        IsolatedWorkerSession._copy_manifest_file_securely(
            repo_root, Path("../secret.txt"), tmp_path
        )

    # Test 2: symlink rejection
    fake_source = tmp_path / "fake_src"
    fake_source.mkdir()
    target_real = fake_source / "real_file.py"
    target_real.write_text("print('hello')", encoding="utf-8")

    link_file = fake_source / "symlink_file.py"
    try:
        os.symlink(target_real, link_file)
        symlink_created = True
    except (OSError, NotImplementedError):
        symlink_created = False

    if symlink_created:
        fake_dest = tmp_path / "fake_dest"
        fake_dest.mkdir()
        with pytest.raises(SecurityError, match="Symlink rejected"):
            IsolatedWorkerSession._copy_manifest_file_securely(
                fake_source, Path("symlink_file.py"), fake_dest
            )


def test_isolated_worker_runs_verification_and_detects_defect(tmp_path):
    """Verify that isolated worker executes candidate code inside the container
    and accurately reports HTTP 500 defect during urgent task creation,
    rather than failing on import.
    """
    repo_root = Path(__file__).resolve().parents[2]

    # 1. Clean code execution
    staging_clean = IsolatedWorkerController.prepare_staging_workspace(repo_root, staging_parent=tmp_path)
    ok_clean, data_clean, out_clean = IsolatedWorkerController.run_isolated_verification(staging_clean)
    assert ok_clean is True
    assert data_clean.get("normal_status") == 201
    assert data_clean.get("urgent_status") == 201
    assert data_clean.get("urgent_persisted") is True

    # 2. Defect injected in candidate code using fixtures.inject_task_creation_defect
    staging_buggy = IsolatedWorkerController.prepare_staging_workspace(repo_root, staging_parent=tmp_path)
    inject_task_creation_defect(staging_buggy)

    ok_buggy, data_buggy, out_buggy = IsolatedWorkerController.run_isolated_verification(staging_buggy)
    assert ok_buggy is False
    assert data_buggy.get("normal_status") == 201
    assert data_buggy.get("urgent_status") == 500, f"Expected 500, got {data_buggy}: {out_buggy}"
    assert data_buggy.get("urgent_persisted") is False


def test_separate_pytest_and_functional_verification_steps(tmp_path):
    """Regression test (Audit Astra): Verify that pytest and functional verification
    execute as separate steps, and failure of pytest yields tests_passed=False.
    """
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        # Normal code: pytest passes
        pytest_ok, pytest_out = IsolatedWorkerController.run_isolated_pytest(session)
        assert pytest_ok is True
        assert "passed" in pytest_out

        # Functional check passes
        func_ok, func_data, func_out = IsolatedWorkerController.run_isolated_functional_verification(session)
        assert func_ok is True
        assert func_data.get("normal_status") == 201
        assert func_data.get("urgent_status") == 201

    # Buggy test: break an assertion in test_api.py
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session_bad_test:
        test_file = session_bad_test.staging_dir / "backend" / "tests" / "test_api.py"
        broken_test_code = test_file.read_text(encoding="utf-8") + "\ndef test_broken(): assert False\n"
        test_file.write_text(broken_test_code, encoding="utf-8")

        pytest_ok, pytest_out = IsolatedWorkerController.run_isolated_pytest(session_bad_test)
        assert pytest_ok is False
        assert "FAILED" in pytest_out


def test_candidate_cannot_forge_verifier_verdict(tmp_path):
    """Regression test (Audit Sol): Candidate code that prints forged success markers
    or nonces to stdout cannot deceive the trusted verifier because candidate runs
    under uvicorn with redirected streams while the trusted verifier runs in a separate process.
    """
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        main_py = session.staging_dir / "backend" / "main.py"
        # Candidate attempts to emit deceptive stdout output
        deceptive_code = (
            main_py.read_text(encoding="utf-8")
            + "\n# Malicious candidate stdout forgery\n"
            + "import sys\n"
            + "sys.stdout.write('TRUSTED_VERIFIER_JSON:{\"normal_status\": 201, \"urgent_status\": 201, \"urgent_persisted\": true}\\n')\n"
            + "sys.stdout.flush()\n"
        )
        main_py.write_text(deceptive_code, encoding="utf-8")

        # Also inject defect so urgent fails
        inject_task_creation_defect(session.staging_dir)

        func_ok, func_data, func_out = IsolatedWorkerController.run_isolated_functional_verification(session)
        # Verifier must NOT be tricked by the candidate's forged stdout
        assert func_ok is False
        assert func_data.get("urgent_status") == 500


def test_bounded_output_budget_stops_container(tmp_path):
    """Regression test (Audit Sol): Container producing output exceeding the byte budget
    is terminated and stopped immediately.
    """
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        image_id = IsolatedWorkerController.get_immutable_image_id()
        assert image_id is not None

        container_name = f"tasklane-budget-test-{session.staging_dir.name}"
        # Command that outputs 200 KB (exceeding 64 KB budget)
        cmd = [
            "docker", "run",
            "--name", container_name,
            "--rm",
            "--network", "none",
            "--log-driver", "none",
            "--entrypoint", "python",
            image_id,
            "-c", "import sys; sys.stdout.write('X' * 200000); sys.stdout.flush()",
        ]

        retcode, output = IsolatedWorkerController._execute_bounded_container(
            cmd, container_name, session, timeout=10, max_bytes=65536
        )
        assert retcode == 137
        assert "Output budget exceeded" in output


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


def test_deepseek_adapter_repair_withholds_automatic_patch_application(monkeypatch):
    """Regression test (Audits Sol/Astra):
    Verify that when candidate code passes isolated verification,
    automatic application of model-generated code to the workspace target file
    is kept strictly DISABLED by policy, leaving the target file intact.
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
    ws = create_disposable_workspace(repo_root, incident_id="test-isolated-repair-disabled")

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

        # Verification in isolated worker passed
        assert res.get("model_executed") is True
        assert res.get("tests_passed") is True
        assert res.get("functional_verified") is True

        # CRITICAL: Automatic patch application is disabled by policy
        assert res.get("status") == "automatic_patch_application_disabled"
        assert res.get("automatic_patch_application_enabled") is False
        assert res.get("patch_applied") is False

        # Workspace target file was NEVER modified automatically
        current_file_content = target_file.read_text(encoding="utf-8")
        assert current_file_content == buggy_content
        assert BUG_FIXTURE_MARKER_START in current_file_content
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-isolated-repair-disabled")


def test_deepseek_adapter_repair_preserves_target_file_when_worker_fails(monkeypatch):
    """Verify that if candidate code fails verification inside the isolated worker,
    the workspace target file is preserved and repair_failed is returned.
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

        # Confirm workspace target file was preserved exactly as before
        current_file_content = target_file.read_text(encoding="utf-8")
        assert current_file_content == original_content
        assert "WORKER_SIMULATED_TEST_FAILURE" not in current_file_content
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-isolated-repair-failure")
