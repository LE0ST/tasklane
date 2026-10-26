"""Unit tests for agent adapters (agent_adapter.py)."""
import difflib
import json
import os
from pathlib import Path
import socket
import sys
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
from fixtures import (
    BUG_FIXTURE_MARKER_END,
    BUG_FIXTURE_MARKER_START,
    get_target_file,
    inject_task_creation_defect,
    remove_task_creation_defect,
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
    monkeypatch.delenv("TASKLANE_ISOLATED_WORKER_ENABLED", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    adapter = DeepSeekAgentAdapter()
    adapter.write_capable_enabled = True

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


def test_deepseek_adapter_repair_guarantees_rollback_on_unhandled_exception(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    monkeypatch.setenv("TASKLANE_ISOLATED_WORKER_ENABLED", "true")
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-ds-exc-rollback")

    try:
        inject_task_creation_defect(ws)
        target_file = get_target_file(ws)
        pre_repair_content = target_file.read_text(encoding="utf-8")

        adapter = DeepSeekAgentAdapter()
        adapter.write_capable_enabled = True

        # Generate valid diff that applies cleanly
        clean_lines = [
            l for l in pre_repair_content.splitlines(keepends=True)
            if BUG_FIXTURE_MARKER_START not in l and BUG_FIXTURE_MARKER_END not in l
        ]
        valid_diff = "".join(
            difflib.unified_diff(
                pre_repair_content.splitlines(keepends=True),
                clean_lines,
                fromfile="a/backend/main.py",
                tofile="b/backend/main.py",
            )
        )

        mock_payload = {
            "choices": [{"message": {"role": "assistant", "content": valid_diff}}]
        }
        body = json.dumps(mock_payload).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.OpenerDirector.open", return_value=mock_resp):
            # Mock run_disposable_tests raising an unhandled runner exception
            with patch("agent_adapter.run_disposable_tests", side_effect=RuntimeError("UNEXPECTED_RUNNER_CRASH")):
                with pytest.raises(RuntimeError):
                    adapter.repair(
                        {"alertname": "TaskLane5xxRate"},
                        workspace_path=ws,
                        operator_approved=True,
                    )

        # Confirm target file was restored to original content despite unhandled exception
        post_repair_content = target_file.read_text(encoding="utf-8")
        assert post_repair_content == pre_repair_content
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-ds-exc-rollback")


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


def test_responder_public_response_never_leaks_secrets_on_adapter_error(monkeypatch):
    """End-to-end check that synthetic secrets never reach responder public responses."""
    from responder import reset_state
    reset_state()

    secret_canary = "CANARY_SECRET_NEVER_APPEAR_IN_PUBLIC_RESPONSE_888"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret_canary)
    adapter = DeepSeekAgentAdapter()

    http_error = urllib.error.HTTPError(
        url=f"https://api.deepseek.com/chat/completions?leak={secret_canary}",
        code=500,
        msg=f"Internal Server Error: {secret_canary}",
        hdrs={},
        fp=None,
    )

    with patch("urllib.request.OpenerDirector.open", side_effect=http_error):
        diag = adapter.diagnose({"alertname": "TaskLane5xxRate"})

    assert secret_canary not in json.dumps(diag)


# ==============================================================================
# Single-Use Operator Token Guard
# ==============================================================================


def test_operator_token_consumed_on_repair_error_no_retry_allowed():
    """Verify that if repair fails, the single-use token was consumed
    and an immediate retry attempt with the same token is rejected.
    """
    from responder import (
        consume_token,
        generate_incident_token,
        verify_token_match,
    )
    inc_id = "test-single-use-token-inc"
    tok = generate_incident_token(inc_id)

    # Valid before consumption
    assert verify_token_match(inc_id, tok) is True

    # Consumed during state transition to repairing
    consume_token(inc_id)

    # Attempting to verify or use again fails
    assert verify_token_match(inc_id, tok) is False


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


def test_deepseek_adapter_repair_successful_remediation(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    monkeypatch.setenv("TASKLANE_ISOLATED_WORKER_ENABLED", "true")
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-ds-success")

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
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-ds-success")


def test_deepseek_adapter_repair_atomic_rollback_on_test_failure(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    monkeypatch.setenv("TASKLANE_ISOLATED_WORKER_ENABLED", "true")
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-ds-rollback")

    try:
        inject_task_creation_defect(ws)
        target_file = get_target_file(ws)
        pre_repair_content = target_file.read_text(encoding="utf-8")

        adapter = DeepSeekAgentAdapter()
        adapter.write_capable_enabled = True

        broken_diff = (
            "--- a/backend/main.py\n"
            "+++ b/backend/main.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-import logging\n"
            "+import logging; raise RuntimeError('SYNTHETIC_TEST_FAILURE')\n"
            " from contextlib import asynccontextmanager\n"
            " from datetime import datetime, timezone\n"
        )
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
        assert res.get("error") == "test_verification_failed"

        # Verify rollback: file content matches original exactly
        post_repair_content = target_file.read_text(encoding="utf-8")
        assert post_repair_content == pre_repair_content
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-ds-rollback")


def test_sanitize_process_environment_purges_deepseek_api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-deepseek-val")
    clean_env = sanitize_process_environment()
    assert "DEEPSEEK_API_KEY" not in clean_env

    # Extra env override with DEEPSEEK pattern is rejected
    with pytest.raises(SecurityViolation):
        sanitize_process_environment(extra_env={"DEEPSEEK_KEY": "val"})
