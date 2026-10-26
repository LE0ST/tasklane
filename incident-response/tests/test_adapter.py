"""Unit tests for agent adapters (agent_adapter.py)."""
from pathlib import Path
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from agent_adapter import CodexAgentAdapter, MockAgentAdapter
from fixtures import inject_task_creation_defect
from sandbox import cleanup_disposable_workspace, create_disposable_workspace, reset_workspace_registry


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
