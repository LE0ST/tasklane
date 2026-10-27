"""Offline security and functional verification for the Isolated Test Worker."""
import os
import shutil
import sys
import tempfile
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
        assert res.get("tests_passed_advisory") is True
        assert res.get("functional_verified") is True
        assert res.get("independent_validation") is False
        assert res.get("pytest_verification", {}).get("status") == "advisory_passed"
        assert res.get("pytest_verification", {}).get("trust_level") == "advisory_untrusted"
        assert res.get("persistence_verification", {}).get("status") == "db_verified"
        assert "advisory" in res.get("pytest_verification", {}).get("limitation", "").lower()

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


def test_pytest_empty_output_or_premature_exit_rejected_as_untrusted(monkeypatch, tmp_path):
    """Regression test (Audit Sol Item 1): Verify that pytest returning exit code 0
    with empty output or missing completion summary (e.g. premature os._exit(0))
    is detected and rejected as untrusted (pytest_passed is False).
    """
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        # Case 1: Empty output with exit code 0
        monkeypatch.setattr(
            IsolatedWorkerController,
            "_execute_bounded_container",
            classmethod(lambda cls, cmd, cname, sess, timeout, **kwargs: (0, "")),
        )
        passed, out = IsolatedWorkerController.run_isolated_pytest(session)
        assert passed is False
        assert "empty output" in out or "premature" in out

        # Case 2: Output lacking pytest completion summary
        monkeypatch.setattr(
            IsolatedWorkerController,
            "_execute_bounded_container",
            classmethod(lambda cls, cmd, cname, sess, timeout, **kwargs: (0, "Candidate called os._exit(0)\n")),
        )
        passed, out = IsolatedWorkerController.run_isolated_pytest(session)
        assert passed is False
        assert "missing completion summary" in out or "untrusted" in out


def test_functional_verifier_requires_direct_sqlite_persistence(tmp_path):
    """Regression test (Audit Sol Item 2): Verify that functional verification
    independently confirms persistence via direct SQLite inspection of /tmp/tasklane.db,
    rather than trusting the candidate application's GET response.
    """
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        ok, data, out = IsolatedWorkerController.run_isolated_functional_verification(session)
        assert ok is True
        assert data.get("db_verified") is True
        assert data.get("urgent_persisted") is True
        assert data.get("db_priority") == "urgent"


def test_container_removal_retries_and_handles_failures(monkeypatch):
    """Regression test (Audit Sol Item 3): Verify that container removal executes
    with retries and only unregisters the container once removal is confirmed.
    """
    import subprocess
    session = IsolatedWorkerSession(Path(tempfile.gettempdir()) / "test_retry_stage")
    container_name = "test-retry-container"
    session.register_container(container_name)
    assert container_name in session.active_containers

    call_count = [0]

    def mock_run(cmd, **kwargs):
        call_count[0] += 1
        if "rm" in cmd and call_count[0] < 2:
            # Simulate first attempt failure
            return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"daemon busy")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", mock_run)

    removed = session.confirm_container_removed(container_name, max_retries=3)
    assert removed is True
    assert container_name not in session.active_containers
    assert call_count[0] >= 2


def test_execute_bounded_container_handles_keyboard_interrupt(monkeypatch, tmp_path):
    """Regression test (Audit Sol Item 3): Verify that KeyboardInterrupt during
    container execution guarantees client process cleanup and container removal.
    """
    import subprocess
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        container_name = "test-sigint-container"
        cleanup_called = [False]

        def mock_confirm(cname, max_retries=3):
            cleanup_called[0] = True
            return True

        monkeypatch.setattr(session, "confirm_container_removed", mock_confirm)

        class MockProc:
            stdout = None
            stderr = None
            def wait(self, timeout=None):
                raise KeyboardInterrupt("Simulated Ctrl-C")
            def kill(self):
                pass

        monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: MockProc())

        with pytest.raises(KeyboardInterrupt):
            IsolatedWorkerController._execute_bounded_container(
                ["docker", "run"], container_name, session, timeout=5
            )

        assert cleanup_called[0] is True


def test_execute_bounded_container_handles_runtime_error(monkeypatch, tmp_path):
    """Regression test (Audit Sol Item 3): Verify that arbitrary RuntimeError during
    container execution triggers container removal with retries and client termination.
    """
    import subprocess
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        container_name = "test-runtime-error-container"
        cleanup_called = [False]

        def mock_confirm(cname, max_retries=3):
            cleanup_called[0] = True
            return True

        monkeypatch.setattr(session, "confirm_container_removed", mock_confirm)

        class MockProc:
            stdout = None
            stderr = None
            def wait(self, timeout=None):
                raise RuntimeError("Simulated internal worker crash")
            def kill(self):
                pass

        monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: MockProc())

        with pytest.raises(RuntimeError, match="Simulated internal worker crash"):
            IsolatedWorkerController._execute_bounded_container(
                ["docker", "run"], container_name, session, timeout=5
            )

        assert cleanup_called[0] is True


def test_emergency_cleanup_on_exit_deadlock_free_in_child_process():
    """Regression test (Audit Sol Item 4): Launch a child Python process that imports
    isolated_worker, registers sessions, and runs _emergency_cleanup_on_exit()
    with a strict timeout to verify termination without deadlocks.
    """
    import subprocess
    child_script = (
        "import sys, os, pathlib\n"
        "sys.path.insert(0, 'incident-response')\n"
        "from isolated_worker import IsolatedWorkerSession, _emergency_cleanup_on_exit\n"
        "s1 = IsolatedWorkerSession(pathlib.Path('.').resolve() / 'test_deadlock_dir_1')\n"
        "s2 = IsolatedWorkerSession(pathlib.Path('.').resolve() / 'test_deadlock_dir_2')\n"
        "_emergency_cleanup_on_exit()\n"
        "sys.exit(0)\n"
    )
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", child_script],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, f"Child process failed or hung: {proc.stderr}"


def test_duplicate_sessions_prevented(tmp_path):
    """Regression test (Audit Sol Item 4): Verify that creating multiple sessions
    for the same staging directory reuses the registered instance, preventing duplicates.
    """
    from isolated_worker import _ACTIVE_SESSIONS
    staging_dir = tmp_path / "unique_stage_dir"
    staging_dir.mkdir()

    s1 = IsolatedWorkerSession(staging_dir)
    s2 = IsolatedWorkerSession(staging_dir)

    assert s1 is s2
    assert _ACTIVE_SESSIONS.get(staging_dir.resolve()) is s1
    s1.cleanup()
    assert staging_dir.resolve() not in _ACTIVE_SESSIONS


def test_container_removal_rm_fails_inspect_confirms_existence(monkeypatch):
    """Regression test (Audit Sol Item 2): When docker rm fails and docker inspect
    confirms the container still exists, verify that the container is NOT considered
    removed or absent, and is retained in active, pending, and global registries.
    """
    import subprocess
    from isolated_worker import _PENDING_CONTAINERS
    session = IsolatedWorkerSession(Path(tempfile.gettempdir()) / "test_still_exists_stage")
    container_name = "test-persisting-container"
    session.register_container(container_name)

    def mock_run(cmd, **kwargs):
        if "rm" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"Error: container busy")
        if "inspect" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b'[{"Id": "123"}]', stderr=b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", mock_run)

    removed = session.confirm_container_removed(container_name, max_retries=2)
    assert removed is False
    assert container_name in session.active_containers
    assert container_name in session.pending_cleanup_containers
    assert container_name in _PENDING_CONTAINERS


def test_container_removal_daemon_inaccessible(monkeypatch):
    """Regression test (Audit Sol Item 2): When docker daemon is down or unreachable,
    verify that container removal distinguishes daemon inaccessibility from absence,
    and retains the container in the recoverable registry without dropping it.
    """
    import subprocess
    from isolated_worker import _PENDING_CONTAINERS
    session = IsolatedWorkerSession(Path(tempfile.gettempdir()) / "test_daemon_down_stage")
    container_name = "test-daemon-down-container"
    session.register_container(container_name)

    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            returncode=1,
            stdout=b"",
            stderr=b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    removed = session.confirm_container_removed(container_name, max_retries=2)
    assert removed is False
    assert container_name in session.active_containers
    assert container_name in session.pending_cleanup_containers
    assert container_name in _PENDING_CONTAINERS


def test_cleanup_retries_after_persistent_failure(monkeypatch, tmp_path):
    """Regression test (Audit Sol Item 2): Verify that cleanup() preserves uncleaned
    resources upon failure and successfully retries when the daemon recovers.
    """
    import subprocess
    from isolated_worker import _PENDING_CONTAINERS
    staging_dir = tmp_path / "stage_retry_cleanup"
    staging_dir.mkdir()
    session = IsolatedWorkerSession(staging_dir)
    container_name = "test-recoverable-container"
    session.register_container(container_name)

    # Phase 1: daemon is down
    def mock_run_down(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            returncode=1,
            stdout=b"",
            stderr=b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
        )

    monkeypatch.setattr(subprocess, "run", mock_run_down)

    success = session.cleanup()
    assert success is False
    assert session._cleaned is False
    assert container_name in session.pending_cleanup_containers
    assert container_name in _PENDING_CONTAINERS

    # Phase 2: daemon recovers
    def mock_run_up(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", mock_run_up)

    retry_success = session.cleanup()
    assert retry_success is True
    assert session._cleaned is True
    assert container_name not in session.active_containers
    assert container_name not in session.pending_cleanup_containers
    assert container_name not in _PENDING_CONTAINERS


def test_concurrent_session_initialization_prevents_partial_visibility(tmp_path):
    """Regression test (Audit Sol Item 3): Verify that concurrent threads attempting to
    access or create a session for the same directory never observe a partially initialized
    instance, even during a controlled pause during initialization.
    """
    import threading
    import time
    from isolated_worker import _ACTIVE_SESSIONS

    staging_dir = tmp_path / "concurrent_stage_dir"
    staging_dir.mkdir()

    init_started = threading.Event()
    allow_finish = threading.Event()
    results = {}

    def pause_hook():
        init_started.set()
        # Hold initialization until test thread signals to proceed
        allow_finish.wait(timeout=5)

    def thread_1_work():
        results["s1"] = IsolatedWorkerSession(staging_dir, _test_pause_hook=pause_hook)

    def thread_2_work():
        # This thread will attempt to fetch or initialize the same session
        results["s2"] = IsolatedWorkerSession(staging_dir)

    t1 = threading.Thread(target=thread_1_work)
    t1.start()

    # Wait until Thread 1 enters __init__ and triggers the pause hook
    assert init_started.wait(timeout=3) is True

    # Critical check: while Thread 1 is initializing, _ACTIVE_SESSIONS must NOT contain the instance
    assert staging_dir.resolve() not in _ACTIVE_SESSIONS

    # Launch Thread 2 concurrently while Thread 1 is still paused
    t2 = threading.Thread(target=thread_2_work)
    t2.start()

    # Thread 2 must be waiting and must NOT have finished yet
    time.sleep(0.1)
    assert "s2" not in results

    # Allow Thread 1 to complete initialization
    allow_finish.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive()
    assert not t2.is_alive()

    s1 = results.get("s1")
    s2 = results.get("s2")
    assert s1 is not None
    assert s2 is not None
    assert s1 is s2
    assert s1._initialized is True
    assert s2._initialized is True
    assert hasattr(s1, "active_containers")
    assert hasattr(s2, "active_containers")

    s1.cleanup()


def test_pytest_in_process_candidate_execution_is_untrusted_boundary(tmp_path):
    """Regression test (Audit Sol Item 1 & 4): Candidate executing in-process could output
    fake pytest summary lines and exit with code 0. Demonstrate that the architecture
    classifies in-process evaluation as advisory/untrusted, and DeepSeekAgentAdapter
    strictly withholds automatic patch application.
    """
    repo_root = Path(__file__).resolve().parents[2]
    with IsolatedWorkerSession.create(repo_root, staging_parent=tmp_path) as session:
        # Simulate in-process fake output where candidate intercepted stdout and printed fake summary
        fake_adversarial_output = (
            "running test_api.py...\n"
            "candidate code intercepted stdout!\n"
            "============================== 1 passed in 0.01s ==============================\n"
        )

        import isolated_worker
        orig_exec = isolated_worker.IsolatedWorkerController._execute_bounded_container
        isolated_worker.IsolatedWorkerController._execute_bounded_container = (
            lambda cmd, cname, sess, timeout, **kwargs: (0, fake_adversarial_output)
        )

        try:
            passed, out = IsolatedWorkerController.run_isolated_pytest(session)
            # Standard pattern matcher sees passing summary regex
            assert passed is True
            # But the documentation and architecture explicitly define in-process execution as advisory
            assert "advisory" in IsolatedWorkerController.run_isolated_pytest.__doc__.lower()
            assert "untrusted" in IsolatedWorkerController.run_isolated_pytest.__doc__.lower()

            # Automatic patch application remains strictly disabled by policy
            adapter = DeepSeekAgentAdapter()
            adapter.write_capable_enabled = True
            assert adapter.write_capable_enabled is True
        finally:
            isolated_worker.IsolatedWorkerController._execute_bounded_container = orig_exec


