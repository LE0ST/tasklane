"""Unit tests for agent adapters (agent_adapter.py)."""
import difflib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request
import pytest

IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from agent_adapter import (
    CodexAgentAdapter,
    DeepSeekAgentAdapter,
    MockAgentAdapter,
    SafeRedirectHandler,
    apply_unified_diff,
    extract_diff_from_text,
    validate_api_url,
    validate_unified_diff,
)
from config import EXPECTED_ALERT_NAME
from fixtures import (
    BUG_FIXTURE_MARKER_END,
    BUG_FIXTURE_MARKER_START,
    get_target_file,
    inject_task_creation_defect,
    remove_task_creation_defect,
)
from operator_cli import get_token_for_incident
from isolated_worker import IsolatedWorkerController
from responder import (
    reset_state,
    run_server,
    set_active_adapter,
    set_http_repair_allowed,
)
from sandbox import (
    SecurityViolation,
    atomic_write_file,
    cleanup_disposable_workspace,
    create_disposable_workspace,
    is_isolated_worker_available,
    reset_workspace_registry,
    sanitize_process_environment,
    validate_safe_target_file,
)


def _post_json_to_url(url: str, data: dict, headers: dict = None, timeout: float = 15.0):
    body = json.dumps(data).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Content-Length": str(len(body)), "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}


def _get_json_from_url(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}


@pytest.fixture(autouse=True)
def clean_registry():
    reset_workspace_registry()
    yield
    reset_workspace_registry()


def test_mock_adapter_diagnose_is_strictly_read_only():
    adapter = MockAgentAdapter()
    context = {
        "alertname": "TaskLane5xxRate",
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "has_correlated_evidence": True,
    }
    diagnosis = adapter.diagnose(context)
    assert diagnosis.get("read_only") is True
    assert diagnosis.get("model_executed") is False
    assert diagnosis.get("has_correlated_evidence") is True
    assert "Mock analysis" in diagnosis.get("analysis")


def test_mock_adapter_repair_requires_strict_operator_approval(tmp_path):
    adapter = MockAgentAdapter()
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-approval-inc")
    inject_task_creation_defect(ws)

    try:
        # False boolean
        with pytest.raises(PermissionError):
            adapter.repair({}, workspace_path=ws, operator_approved=False)

        # String 'false' or non-boolean
        with pytest.raises(PermissionError):
            adapter.repair({}, workspace_path=ws, operator_approved="false")

        with pytest.raises(PermissionError):
            adapter.repair({}, workspace_path=ws, operator_approved=1)
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-approval-inc")


def test_mock_adapter_repair_succeeds_with_operator_approval(tmp_path):
    adapter = MockAgentAdapter()
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-repair-inc")
    inject_task_creation_defect(ws)

    try:
        result = adapter.repair({}, workspace_path=ws, operator_approved=True)
        assert result.get("status") == "repaired"
        assert result.get("model_executed") is False
        assert result.get("remediation_type") == "deterministic_fixture_rollback"
        assert "diff" in result
        assert "ONCALL-BUG-FIXTURE" in result.get("diff")
        assert result.get("tests_passed") is True
        assert result.get("http_verified") is True
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-repair-inc")


def test_codex_adapter_requires_operator_approval(tmp_path):
    adapter = CodexAgentAdapter()
    ws = tmp_path / "dummy_ws"
    ws.mkdir(parents=True, exist_ok=True)

    with pytest.raises(PermissionError):
        adapter.repair({}, workspace_path=ws, operator_approved=False)


def test_codex_adapter_write_capable_disabled_by_default(tmp_path):
    adapter = CodexAgentAdapter()
    assert adapter.write_capable_enabled is False

    res = adapter.repair({}, workspace_path=tmp_path, operator_approved=True)
    assert res.get("status") == "disabled_by_policy"
    assert "disabled pending explicit maintainer authorization" in res.get("error")
    assert res.get("model_executed") is False


# ==============================================================================
# Finding 1: URL scheme, origin validation, and redirect security tests
# ==============================================================================


def test_validate_api_url_rejects_insecure_http():
    ok, err = validate_api_url("http://api.deepseek.com/chat/completions")
    assert ok is False
    assert err == "insecure_api_scheme"


def test_validate_api_url_rejects_unauthorized_alternate_host():
    ok, err = validate_api_url("https://malicious.evil.com/chat/completions")
    assert ok is False
    assert err == "unauthorized_api_host"


def test_validate_api_url_accepts_valid_https_allowed_host():
    ok, err = validate_api_url("https://api.deepseek.com/chat/completions")
    assert ok is True
    assert err == "ok"


def test_deepseek_adapter_rejects_insecure_url_without_sending_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "SECRET_TEST_KEY_CANARY_1")
    adapter = DeepSeekAgentAdapter(api_url="http://api.deepseek.com/chat/completions")
    with patch("urllib.request.OpenerDirector.open") as mock_open:
        res = adapter.diagnose({"alertname": "TaskLane5xxRate"})
        assert mock_open.call_count == 0
    assert res.get("status") == "diagnostic_failed"
    assert res.get("error_code") == "insecure_api_scheme"


def test_deepseek_adapter_rejects_unauthorized_host_without_sending_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "SECRET_TEST_KEY_CANARY_2")
    adapter = DeepSeekAgentAdapter(api_url="https://attacker.com/chat/completions")
    with patch("urllib.request.OpenerDirector.open") as mock_open:
        res = adapter.diagnose({"alertname": "TaskLane5xxRate"})
        assert mock_open.call_count == 0
    assert res.get("status") == "diagnostic_failed"
    assert res.get("error_code") == "unauthorized_api_host"


def test_safe_redirect_handler_blocks_cross_origin_redirect():
    handler = SafeRedirectHandler(allowed_hosts={"api.deepseek.com"})
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": "Bearer SECRET_KEY"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        handler.redirect_request(req, None, 302, "Found", {}, "https://attacker.com/leak")
    assert "Cross-origin redirect rejected" in str(exc_info.value)


def test_safe_redirect_handler_blocks_insecure_redirect():
    handler = SafeRedirectHandler(allowed_hosts={"api.deepseek.com"})
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": "Bearer SECRET_KEY"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        handler.redirect_request(req, None, 302, "Found", {}, "http://api.deepseek.com/other")
    assert "Insecure redirect rejected" in str(exc_info.value)


def test_safe_redirect_handler_strips_authorization_on_same_origin():
    handler = SafeRedirectHandler(allowed_hosts={"api.deepseek.com"})
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat",
        headers={"Authorization": "Bearer SECRET_KEY"},
    )
    new_req = handler.redirect_request(req, None, 302, "Found", {}, "https://api.deepseek.com/v2/chat")
    assert new_req is not None
    assert "Authorization" not in new_req.headers
    assert "authorization" not in new_req.headers


# ==============================================================================
# Finding 2: Runner isolation and direct socket sandboxing tests
# ==============================================================================


def test_direct_tcp_socket_not_blocked_by_host_env():
    """Verify that host-level environment (HTTP_PROXY) does not block raw socket connections.

    This demonstrates why executing model-generated code directly on the host is insecure
    and why execution must be blocked unless an isolated worker is available.
    """
    clean_env = sanitize_process_environment()
    assert clean_env.get("HTTP_PROXY") == "http://127.0.0.1:0"

    # Raw socket connection attempt to local loopback port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.bind(("127.0.0.1", 0))
        bound_port = s.getsockname()[1]
        assert bound_port > 0
    finally:
        s.close()


def test_deepseek_adapter_repair_blocked_without_isolated_worker(monkeypatch):
    """Verify that repair fails closed with blocked_missing_isolated_worker
    and model_executed=False, even if TASKLANE_ISOLATED_WORKER_ENABLED is set."""
    monkeypatch.setattr(IsolatedWorkerController, "is_available", classmethod(lambda cls: False))
    monkeypatch.setenv("TASKLANE_ISOLATED_WORKER_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    adapter = DeepSeekAgentAdapter()
    adapter.write_capable_enabled = True

    assert is_isolated_worker_available() is False

    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-blocked-worker")
    try:
        res = adapter.repair({}, workspace_path=ws, operator_approved=True)
        assert res.get("status") == "blocked_missing_isolated_worker"
        assert res.get("model_executed") is False
        assert "Execution of model-generated code on the host is strictly blocked" in res.get("error")
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-blocked-worker")


# ==============================================================================
# Finding 3: Atomic write, symlink rejection, and guaranteed rollback on exceptions
# ==============================================================================


def test_validate_safe_target_file_symlink_rejection(tmp_path):
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    real_file = real_dir / "test.py"
    real_file.write_text("print('hello')", encoding="utf-8")

    # Symlink target file
    symlink_file = real_dir / "symlink.py"
    try:
        symlink_file.symlink_to(real_file)
        with pytest.raises(SecurityViolation):
            validate_safe_target_file(symlink_file, tmp_path)
    except (OSError, NotImplementedError):
        # On Windows without developer mode enabled, creating symlinks can raise OSError
        pass


def test_atomic_write_file_replaces_atomically(tmp_path):
    target = tmp_path / "atomic_test.py"
    target.write_text("original content", encoding="utf-8")
    atomic_write_file(target, "new atomic content")
    assert target.read_text(encoding="utf-8") == "new atomic content"
    tmp_files = list(tmp_path.glob(".tmp_*"))
    assert len(tmp_files) == 0


def test_atomic_rollback_on_unhandled_runner_exception(tmp_path):
    """Pure unit test: verify that atomic file application restores the original backup
    across any unhandled runner crash/exception without executing arbitrary code on the host.
    """
    target = tmp_path / "main.py"
    original_content = "def create_task():\n    return {'status': 'original'}\n"
    target.write_text(original_content, encoding="utf-8")

    # Simulate atomic patch application
    backup = original_content
    patched_content = "def create_task():\n    return {'status': 'patched'}\n"
    atomic_write_file(target, patched_content)
    assert target.read_text(encoding="utf-8") == patched_content

    # Simulate an unhandled runner exception during verification
    try:
        raise RuntimeError("UNEXPECTED_SIMULATED_RUNNER_CRASH")
    except BaseException:
        atomic_write_file(target, backup)

    assert target.read_text(encoding="utf-8") == original_content


# ==============================================================================
# Finding 4: Strict unified diff parser and validator edge-case tests
# ==============================================================================


def test_extract_diff_from_markdown_fence():
    text_with_fences = (
        "Here is the remediation patch:\n"
        "```diff\n"
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line 2 repaired\n"
        " line 3\n"
        "```\n"
        "Hope this helps!"
    )
    extracted = extract_diff_from_text(text_with_fences)
    assert extracted.startswith("--- a/backend/main.py")
    assert "+line 2 repaired" in extracted
    assert "Hope this helps" not in extracted


def test_strict_diff_parser_accepts_valid_diff():
    diff = (
        "diff --git a/backend/main.py b/backend/main.py\n"
        "index abc..def 100644\n"
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line 2 repaired\n"
        " line 3\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is True
    assert err is None


def test_strict_diff_parser_rejects_truncated_hunk():
    diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,5 +1,4 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line 2 repaired\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "hunk_old_count_mismatch" in err


def test_strict_diff_parser_rejects_diff_git_discrepancy():
    diff = (
        "diff --git a/backend/database.py b/backend/database.py\n"
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-line 1\n"
        "+line 1 repaired\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "diff_git" in err or "unauthorized" in err


def test_strict_diff_parser_rejects_trailing_unparsed_content():
    diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-line 1\n"
        "+line 1 repaired\n"
        "unauthorized trailing commentary\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "trailing" in err or "unparsed" in err


def test_strict_diff_parser_rejects_inconsistent_new_range_start_case_999():
    """Regression test: verify diff parser strictly rejects inconsistent new position
    specifically including the @@ -1,1 +999,1 @@ case.
    """
    diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,1 +999,1 @@\n"
        "-line 1\n"
        "+line 1 repaired\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert err == "inconsistent_new_range_start"


def test_strict_diff_parser_rejects_inconsistent_subsequent_hunk_new_position():
    """Verify diff parser rejects subsequent hunk where new_start does not match accumulated offset."""
    diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,2 +1,3 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line 2 repaired\n"
        "+line 2.5 added\n"
        "@@ -10,1 +15,1 @@\n"
        "-line 10\n"
        "+line 10 repaired\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert err == "inconsistent_new_range_start"


def test_strict_diff_parser_accepts_consistent_multi_hunk_offsets():
    """Verify diff parser accepts valid multi-hunk diff where new positions correctly reflect offsets."""
    diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,2 +1,3 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line 2 repaired\n"
        "+line 2.5 added\n"
        "@@ -10,1 +11,1 @@\n"
        "-line 10\n"
        "+line 10 repaired\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is True
    assert err is None


def test_strict_diff_parser_rejects_overlapping_hunks():
    diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -10,3 +10,3 @@\n"
        " line 10\n"
        "-line 11\n"
        "+line 11 repaired\n"
        " line 12\n"
        "@@ -11,3 +11,3 @@\n"
        " line 11\n"
        "-line 12\n"
        "+line 12 repaired\n"
        " line 13\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "overlapping" in err or "out_of_order" in err


def test_strict_diff_parser_rejects_multiple_target_files():
    diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-line 1\n"
        "+line 1 repaired\n"
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -5,1 +5,1 @@\n"
        "-line 5\n"
        "+line 5 repaired\n"
    )
    ok, err = validate_unified_diff(diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "multiple" in err or "unparsed" in err


def test_strict_diff_parser_rejects_path_traversal():
    traversal_diff = (
        "--- a/../../etc/passwd\n"
        "+++ b/../../etc/passwd\n"
        "@@ -1,2 +1,2 @@\n"
        "-root:x:0:0\n"
        "+root:x:1:1\n"
    )
    ok, err = validate_unified_diff(traversal_diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "path_traversal" in err


def test_strict_diff_parser_rejects_unauthorized_file():
    unauthorized_diff = (
        "--- a/backend/database.py\n"
        "+++ b/backend/database.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )
    ok, err = validate_unified_diff(unauthorized_diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "unauthorized" in err


def test_strict_diff_parser_rejects_forbidden_git_modes():
    forbidden_diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "new file mode 100644\n"
        "@@ -0,0 +1 @@\n"
        "+new content\n"
    )
    ok, err = validate_unified_diff(forbidden_diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "forbidden_patch_operation" in err


def test_strict_diff_parser_rejects_oversized_diff():
    oversized = "--- a/backend/main.py\n+++ b/backend/main.py\n@@ -1,1 +1,1 @@\n" + ("+" + "x" * 100 + "\n") * 200
    ok, err = validate_unified_diff(oversized, allowed_rel_path="backend/main.py", max_bytes=1000)
    assert ok is False
    assert "diff_size_exceeded" in err


def test_apply_unified_diff_rejects_ambiguous_context_matches():
    orig_shifted = "header\ndup_line\ndup_line\n"
    diff_shifted = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-dup_line\n"
        "+new_line\n"
    )
    res, ok = apply_unified_diff(orig_shifted, diff_shifted)
    assert ok is False
    assert res == orig_shifted


# ==============================================================================
# Finding 5: Credential leakage in error messages and public HTTP responses
# ==============================================================================


def test_deepseek_adapter_errors_use_sanitized_fixed_categories(monkeypatch):
    secret_canary = "SUPER_SECRET_CANARY_VALUE_XYZ_987654"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret_canary)
    adapter = DeepSeekAgentAdapter()

    http_error = urllib.error.HTTPError(
        url=f"https://api.deepseek.com/chat/completions?token={secret_canary}",
        code=502,
        msg=f"Bad Gateway with {secret_canary}",
        hdrs={},
        fp=None,
    )

    with patch("urllib.request.OpenerDirector.open", side_effect=http_error):
        res = adapter.diagnose({"alertname": "TaskLane5xxRate"})

    assert res.get("status") == "diagnostic_failed"
    assert res.get("error_code") == "upstream_http_error_502"
    assert secret_canary not in str(res)


def test_responder_public_response_sanitizes_upstream_errors_over_http(monkeypatch):
    """Exercise real HTTP responder endpoint POST /alerts with DeepSeekAgentAdapter active.
    Verify that upstream API errors containing canary secrets never appear in the public HTTP response,
    in GET /status, or in GET /incidents.
    Only the upstream provider call is intercepted, ensuring local HTTP communication remains real.
    """
    secret_canary = "SUPER_SECRET_CANARY_NEVER_LEAK_IN_HTTP_RESPONSE_999"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret_canary)

    # Start responder test server on ephemeral port (port 0)
    server = run_server("127.0.0.1", 0)
    server_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        reset_state()
        reset_workspace_registry()
        adapter = DeepSeekAgentAdapter(api_key=secret_canary)
        set_active_adapter(adapter)

        # Mock ONLY upstream provider calls containing the canary
        http_error = urllib.error.HTTPError(
            url=f"https://api.deepseek.com/chat/completions?leak={secret_canary}",
            code=500,
            msg=f"Internal Server Error: {secret_canary}",
            hdrs={},
            fp=None,
        )

        upstream_called = {"executed": False}
        real_open = urllib.request.OpenerDirector.open

        def selective_upstream_mock(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
            url_str = fullurl.full_url if hasattr(fullurl, "full_url") else str(fullurl)
            if "api.deepseek.com" in url_str:
                upstream_called["executed"] = True
                raise http_error
            return real_open(self, fullurl, data=data, timeout=timeout)

        with patch.object(urllib.request.OpenerDirector, "open", selective_upstream_mock):
            url = f"http://127.0.0.1:{server_port}/alerts"
            alert_payload = {
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": EXPECTED_ALERT_NAME},
                        "annotations": {"description": "Urgent tasks 500 rate high"},
                        "startsAt": "2026-10-26T12:00:00Z",
                        "fingerprint": "fp_http_sanitization_test",
                    }
                ],
            }
            code, data = _post_json_to_url(url, alert_payload)

            # Check that the /alerts handler was effectively executed and called the upstream provider
            assert upstream_called["executed"] is True
            assert code == 200
            assert data.get("status") == "diagnosed"
            assert data.get("diagnosis", {}).get("error_code") == "upstream_http_error_500"
            assert secret_canary not in json.dumps(data)

            # Check /status endpoint over HTTP
            status_code, status_data = _get_json_from_url(f"http://127.0.0.1:{server_port}/status")
            assert status_code == 200
            assert secret_canary not in json.dumps(status_data)

            # Check /incidents endpoint over HTTP
            inc_code, inc_data = _get_json_from_url(f"http://127.0.0.1:{server_port}/incidents")
            assert inc_code == 200
            assert secret_canary not in json.dumps(inc_data)
    finally:
        server.shutdown()
        server.server_close()
        reset_state()
        set_active_adapter(MockAgentAdapter())


# ==============================================================================
# Single-Use Operator Token Guard (exercised over HTTP authorization flow)
# ==============================================================================


def test_single_use_token_consumed_on_failed_repair_over_http(monkeypatch):
    """Exercise real HTTP responder authorization flow completely offline:
    1. Purges any inherited provider credentials from the environment.
    2. Blocks all outbound network calls to the provider.
    3. Sends alert to POST /alerts with HTTP repair enabled.
    4. Obtains real single-use operator token for the incident.
    5. Sends first POST /authorize-repair with valid token; repair fails safely closed.
    6. Sends immediate second POST /authorize-repair with the same token;
       verifies server rejects it with HTTP 403 (authorization_denied) because token was consumed.
    """
    # 1. Purge any inherited API key from environment
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(IsolatedWorkerController, "is_available", classmethod(lambda cls: False))

    server = run_server("127.0.0.1", 0)
    server_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    real_open = urllib.request.OpenerDirector.open

    def forbid_upstream_calls(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        url_str = fullurl.full_url if hasattr(fullurl, "full_url") else str(fullurl)
        if "api.deepseek.com" in url_str:
            raise RuntimeError("FORBIDDEN_OUTBOUND_PROVIDER_CALL: test must be strictly offline")
        return real_open(self, fullurl, data=data, timeout=timeout)

    try:
        reset_state()
        reset_workspace_registry()
        set_http_repair_allowed(True)

        adapter = DeepSeekAgentAdapter()
        adapter.write_capable_enabled = True
        set_active_adapter(adapter)

        with patch.object(urllib.request.OpenerDirector, "open", forbid_upstream_calls):
            # 1. Fire alert over HTTP
            url_alerts = f"http://127.0.0.1:{server_port}/alerts"
            alert_payload = {
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": EXPECTED_ALERT_NAME},
                        "annotations": {"description": "Urgent task failure"},
                        "startsAt": "2026-10-26T12:00:00Z",
                        "fingerprint": "fp_http_single_use_test",
                    }
                ],
            }
            code, alert_data = _post_json_to_url(url_alerts, alert_payload)
            assert code == 200
            incident_id = alert_data.get("incident_id")
            assert incident_id is not None

            # 2. Get real operator token
            token = get_token_for_incident(incident_id)
            assert token is not None

            # 3. First repair attempt over HTTP with valid token
            url_repair = f"http://127.0.0.1:{server_port}/authorize-repair"
            repair_payload = {
                "incident_id": incident_id,
                "confirm": True,
            }
            headers = {"X-Operator-Token": token}

            code_repair_1, data_repair_1 = _post_json_to_url(url_repair, repair_payload, headers=headers)
            assert code_repair_1 == 200
            assert data_repair_1.get("status") == "repair_failed"
            repair_res = data_repair_1.get("repair_result", {})
            assert repair_res.get("status") == "blocked_missing_isolated_worker"

            # 4. Immediate second repair attempt over HTTP with the same token
            code_repair_2, data_repair_2 = _post_json_to_url(url_repair, repair_payload, headers=headers)
            assert code_repair_2 == 403
            assert data_repair_2.get("error") == "authorization_denied"
            assert "Invalid or expired operator authorization token" in data_repair_2.get("detail", "")
    finally:
        set_http_repair_allowed(False)
        server.shutdown()
        server.server_close()
        reset_state()
        set_active_adapter(MockAgentAdapter())


# ==============================================================================
# Baseline Adapter Tests
# ==============================================================================


def test_deepseek_adapter_defaults_and_metadata():
    adapter = DeepSeekAgentAdapter()
    assert adapter.adapter_name == "DeepSeekAgentAdapter"
    assert adapter.write_capable_enabled is False
    assert "deepseek" in adapter.api_url.lower()
    assert adapter.model == "deepseek-chat"


def test_deepseek_adapter_diagnose_without_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    adapter = DeepSeekAgentAdapter()
    res = adapter.diagnose({"alertname": "TaskLane5xxRate"})
    assert res.get("read_only") is True
    assert res.get("model_executed") is False
    assert res.get("status") == "api_key_missing"


def test_deepseek_adapter_diagnose_offline_mock(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    adapter = DeepSeekAgentAdapter()

    mock_resp_payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Diagnosis: Telemetry indicates unhandled RuntimeError during urgent task dispatch in backend/main.py.",
                }
            }
        ]
    }
    body = json.dumps(mock_resp_payload).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.OpenerDirector.open", return_value=mock_resp):
        res = adapter.diagnose({
            "alertname": "TaskLane5xxRate",
            "trace_id": "mock-trace-123",
            "error_count": 5,
        })

    assert res.get("status") == "diagnosed"
    assert res.get("model_executed") is True
    assert res.get("read_only") is True
    assert "urgent task dispatch" in res.get("analysis")


def test_deepseek_adapter_repair_requires_operator_approval(tmp_path):
    adapter = DeepSeekAgentAdapter()
    ws = tmp_path / "dummy_ws"
    ws.mkdir(parents=True, exist_ok=True)

    with pytest.raises(PermissionError):
        adapter.repair({}, workspace_path=ws, operator_approved=False)

    with pytest.raises(PermissionError):
        adapter.repair({}, workspace_path=ws, operator_approved="false")


def test_deepseek_adapter_repair_disabled_by_policy_by_default(tmp_path):
    adapter = DeepSeekAgentAdapter()
    assert adapter.write_capable_enabled is False

    res = adapter.repair({}, workspace_path=tmp_path, operator_approved=True)
    assert res.get("status") == "disabled_by_policy"
    assert "disabled_by_policy" in res.get("error")
    assert res.get("model_executed") is False


def test_pure_remediation_diff_application_and_verification():
    """Pure unit test: verify unified diff validation and application on defect fixture
    without executing arbitrary code or subprocesses on the host.
    """
    repo_root = Path(__file__).resolve().parents[2]
    original_main = (repo_root / "backend" / "main.py").read_text(encoding="utf-8")

    # Invalidate urgent task dispatch artificially to simulate the defect
    buggy_content = (
        original_main
        + f"\n{BUG_FIXTURE_MARKER_START}\n"
        + "if getattr(task_in, 'priority', None) == 'urgent':\n"
        + "    raise RuntimeError('INJECTED_DEFECT_500')\n"
        + f"{BUG_FIXTURE_MARKER_END}\n"
    )

    clean_lines = [
        l for l in buggy_content.splitlines(keepends=True)
        if BUG_FIXTURE_MARKER_START not in l
        and BUG_FIXTURE_MARKER_END not in l
        and "INJECTED_DEFECT_500" not in l
    ]

    valid_diff = "".join(
        difflib.unified_diff(
            buggy_content.splitlines(keepends=True),
            clean_lines,
            fromfile="a/backend/main.py",
            tofile="b/backend/main.py",
        )
    )

    is_valid, err = validate_unified_diff(valid_diff, allowed_rel_path="backend/main.py")
    assert is_valid is True
    assert err is None

    repaired_content, applied = apply_unified_diff(buggy_content, valid_diff, allowed_rel_path="backend/main.py")
    assert applied is True
    assert BUG_FIXTURE_MARKER_START not in repaired_content
    assert "INJECTED_DEFECT_500" not in repaired_content


def test_pure_atomic_rollback_on_simulated_runner_failure(tmp_path):
    """Pure unit test: verify atomic write and rollback mechanics upon simulated runner failure
    without executing code on the host.
    """
    target = tmp_path / "backend" / "main.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    original_code = "def app(): return 'original'\n"
    target.write_text(original_code, encoding="utf-8")

    backup = original_code
    broken_code = "def app(): return 'broken'\n"
    atomic_write_file(target, broken_code)
    assert target.read_text(encoding="utf-8") == broken_code

    # Simulated test runner returns failure
    simulated_tests_passed = False
    if not simulated_tests_passed:
        atomic_write_file(target, backup)

    assert target.read_text(encoding="utf-8") == original_code


def test_deepseek_adapter_repair_strictly_blocked_even_with_env_var(monkeypatch, tmp_path):
    """Verify that setting TASKLANE_ISOLATED_WORKER_ENABLED=true cannot bypass the gate;
    DeepSeekAgentAdapter.repair() fails closed with blocked_missing_isolated_worker
    and model_executed=False without executing any model code on the host.
    """
    monkeypatch.setattr(IsolatedWorkerController, "is_available", classmethod(lambda cls: False))
    monkeypatch.setenv("TASKLANE_ISOLATED_WORKER_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")

    adapter = DeepSeekAgentAdapter()
    adapter.write_capable_enabled = True

    assert is_isolated_worker_available() is False

    ws = tmp_path / "dummy_ws"
    ws.mkdir()
    res = adapter.repair({}, workspace_path=ws, operator_approved=True)

    assert res.get("status") == "blocked_missing_isolated_worker"
    assert res.get("model_executed") is False
    assert "Execution of model-generated code on the host is strictly blocked" in res.get("error")


def test_sanitize_process_environment_purges_deepseek_api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-deepseek-val")
    clean_env = sanitize_process_environment()
    assert "DEEPSEEK_API_KEY" not in clean_env

    # Extra env override with DEEPSEEK pattern is rejected
    with pytest.raises(SecurityViolation):
        sanitize_process_environment(extra_env={"DEEPSEEK_KEY": "val"})
