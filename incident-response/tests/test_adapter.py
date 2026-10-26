"""Unit tests for agent adapters (agent_adapter.py)."""
from pathlib import Path
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from agent_adapter import (
    CodexAgentAdapter,
    DeepSeekAgentAdapter,
    MockAgentAdapter,
    apply_unified_diff,
    extract_diff_from_text,
    validate_unified_diff,
)
from fixtures import get_target_file, inject_task_creation_defect, remove_task_creation_defect
from sandbox import (
    SecurityViolation,
    cleanup_disposable_workspace,
    create_disposable_workspace,
    reset_workspace_registry,
    sanitize_process_environment,
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
# Unified diff parsing and security validation tests
# ==============================================================================


def test_extract_diff_from_markdown_fence():
    text_with_fences = (
        "Here is the remediation patch:\n"
        "```diff\n"
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -10,4 +10,4 @@\n"
        "-old\n"
        "+new\n"
        "```\n"
        "Hope this helps!"
    )
    extracted = extract_diff_from_text(text_with_fences)
    assert extracted.startswith("--- a/backend/main.py")
    assert "+new" in extracted
    assert "Hope this helps" not in extracted


def test_validate_unified_diff_allows_valid_target():
    valid_diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_repaired\n"
        " line3\n"
    )
    ok, err = validate_unified_diff(valid_diff, allowed_rel_path="backend/main.py")
    assert ok is True
    assert err is None


def test_validate_unified_diff_rejects_path_traversal():
    traversal_diff = (
        "--- a/../../etc/passwd\n"
        "+++ b/../../etc/passwd\n"
        "@@ -1,2 +1,2 @@\n"
        "-root:x:0:0\n"
        "+root:x:1:1\n"
    )
    ok, err = validate_unified_diff(traversal_diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "Path traversal or absolute path" in err


def test_validate_unified_diff_rejects_unauthorized_file():
    unauthorized_diff = (
        "--- a/backend/database.py\n"
        "+++ b/backend/database.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )
    ok, err = validate_unified_diff(unauthorized_diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "Diff targets unauthorized file" in err


def test_validate_unified_diff_rejects_forbidden_git_modes():
    forbidden_diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "new file mode 100644\n"
        "@@ -0,0 +1 @@\n"
        "+new content\n"
    )
    ok, err = validate_unified_diff(forbidden_diff, allowed_rel_path="backend/main.py")
    assert ok is False
    assert "forbidden patch operation" in err


def test_validate_unified_diff_rejects_oversized_diff():
    oversized = "--- a/backend/main.py\n+++ b/backend/main.py\n@@ -1,1 +1,1 @@\n" + ("+" + "x" * 100 + "\n") * 200
    ok, err = validate_unified_diff(oversized, allowed_rel_path="backend/main.py", max_bytes=1000)
    assert ok is False
    assert "exceeds maximum limit" in err


def test_apply_unified_diff_success_and_failure():
    orig = "line 1\nline 2\nline 3\n"
    diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line 2 repaired\n"
        " line 3\n"
    )
    res, ok = apply_unified_diff(orig, diff)
    assert ok is True
    assert "line 2 repaired" in res

    # Mismatched context fails cleanly
    mismatched_diff = (
        "--- a/backend/main.py\n"
        "+++ b/backend/main.py\n"
        "@@ -1,3 +1,3 @@\n"
        " totally different line\n"
        "-line 2\n"
        "+line 2 repaired\n"
    )
    res_fail, ok_fail = apply_unified_diff(orig, mismatched_diff)
    assert ok_fail is False
    assert res_fail == orig


# ==============================================================================
# DeepSeekAgentAdapter offline unit tests
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
    from unittest.mock import MagicMock, patch
    import json

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

    with patch("urllib.request.urlopen", return_value=mock_resp):
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
    assert "disabled pending explicit maintainer authorization" in res.get("error")
    assert res.get("model_executed") is False


def test_deepseek_adapter_repair_rejects_missing_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-ds-nokey")

    try:
        adapter = DeepSeekAgentAdapter()
        adapter.write_capable_enabled = True

        res = adapter.repair({}, workspace_path=ws, operator_approved=True)
        assert res.get("status") == "api_key_missing"
        assert res.get("model_executed") is False
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-ds-nokey")


def test_deepseek_adapter_repair_rejection_on_path_traversal(monkeypatch):
    from unittest.mock import MagicMock, patch
    import json

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-ds-traversal")

    try:
        inject_task_creation_defect(ws)
        adapter = DeepSeekAgentAdapter()
        adapter.write_capable_enabled = True

        traversal_diff = (
            "--- a/backend/database.py\n"
            "+++ b/backend/database.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-import os\n"
            "+import sys\n"
        )
        mock_payload = {
            "choices": [{"message": {"role": "assistant", "content": traversal_diff}}]
        }
        body = json.dumps(mock_payload).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = adapter.repair({}, workspace_path=ws, operator_approved=True)

        assert res.get("status") == "invalid_diff"
        assert "targets unauthorized file" in res.get("error")
        assert res.get("tests_passed") is False
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-ds-traversal")


def test_deepseek_adapter_repair_successful_remediation(monkeypatch):
    import difflib
    from unittest.mock import MagicMock, patch
    import json
    from fixtures import BUG_FIXTURE_MARKER_START, BUG_FIXTURE_MARKER_END

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-ds-success")

    try:
        inject_task_creation_defect(ws)
        target_file = get_target_file(ws)
        buggy_content = target_file.read_text(encoding="utf-8")

        # Derive clean content and matching unified diff
        clean_lines = []
        in_bug = False
        for l in buggy_content.splitlines(keepends=True):
            if BUG_FIXTURE_MARKER_START in l:
                in_bug = True
                continue
            if BUG_FIXTURE_MARKER_END in l:
                in_bug = False
                continue
            if not in_bug:
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

        with patch("urllib.request.urlopen", return_value=mock_resp):
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
    from unittest.mock import MagicMock, patch
    import json

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, incident_id="test-ds-rollback")

    try:
        inject_task_creation_defect(ws)
        target_file = get_target_file(ws)
        pre_repair_content = target_file.read_text(encoding="utf-8")

        adapter = DeepSeekAgentAdapter()
        adapter.write_capable_enabled = True

        # Valid diff that applies cleanly but causes syntax/runtime test failure
        broken_diff = (
            "--- a/backend/main.py\n"
            "+++ b/backend/main.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-import logging\n"
            "+import logging; raise RuntimeError('SYNTHETIC_TEST_FAILURE')\n"
            " from contextlib import asynccontextmanager\n"
        )
        mock_payload = {
            "choices": [{"message": {"role": "assistant", "content": broken_diff}}]
        }
        body = json.dumps(mock_payload).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = adapter.repair(
                {"alertname": "TaskLane5xxRate", "trace_id": "test-trace"},
                workspace_path=ws,
                operator_approved=True,
            )

        assert res.get("status") == "repair_failed"
        assert res.get("tests_passed") is False
        assert "rolled back" in res.get("error")

        # Verify rollback: file content matches original exactly
        post_repair_content = target_file.read_text(encoding="utf-8")
        assert post_repair_content == pre_repair_content
    finally:
        cleanup_disposable_workspace(ws, incident_id="test-ds-rollback")


def test_deepseek_adapter_api_error_sanitizes_credentials(monkeypatch):
    import urllib.error
    from unittest.mock import patch

    secret_key = "DEEPSEEK_SECRET_KEY_NEVER_LEAK_ME"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret_key)
    adapter = DeepSeekAgentAdapter()

    http_error = urllib.error.HTTPError(
        url=f"https://api.deepseek.com/chat/completions?auth={secret_key}",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        res = adapter.diagnose({"alertname": "TaskLane5xxRate"})

    assert res.get("status") == "diagnostic_failed"
    assert secret_key not in str(res.get("detail"))


def test_sanitize_process_environment_purges_deepseek_api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-deepseek-val")
    clean_env = sanitize_process_environment()
    assert "DEEPSEEK_API_KEY" not in clean_env

    # Extra env override with DEEPSEEK pattern is rejected
    with pytest.raises(SecurityViolation):
        sanitize_process_environment(extra_env={"DEEPSEEK_KEY": "val"})
