"""Unit tests for agent adapters (agent_adapter.py)."""
from pathlib import Path
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from agent_adapter import CodexAgentAdapter, MockAgentAdapter
from fixtures import inject_task_creation_defect
from sandbox import cleanup_disposable_workspace, create_disposable_workspace


def test_mock_adapter_diagnose_is_strictly_read_only():
    adapter = MockAgentAdapter()
    context = {
        "alertname": "TaskLane5xxRate",
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "recent_logs": ["[ERROR] REGRESSION in task creation pipeline: 500"],
    }
    diagnosis = adapter.diagnose(context)
    assert diagnosis.get("status") == "diagnosed"
    assert diagnosis.get("read_only") is True
    assert diagnosis.get("file_modified") is False
    assert "backend/main.py" in diagnosis.get("suspected_file")


def test_mock_adapter_repair_requires_operator_approval(tmp_path):
    adapter = MockAgentAdapter()
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, base_dir=tmp_path)
    inject_task_creation_defect(ws)

    try:
        # Without approval: must raise PermissionError
        with pytest.raises(PermissionError) as exc_info:
            adapter.repair({}, workspace_path=ws, operator_approved=False)
        assert "operator authorization" in str(exc_info.value)
    finally:
        cleanup_disposable_workspace(ws, base_dir=tmp_path)


def test_mock_adapter_repair_succeeds_with_operator_approval(tmp_path):
    adapter = MockAgentAdapter()
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, base_dir=tmp_path)
    inject_task_creation_defect(ws)

    try:
        result = adapter.repair({}, workspace_path=ws, operator_approved=True)
        assert result.get("status") == "repaired"
        assert result.get("operator_approved") is True
        assert "diff" in result
        assert "ONCALL-BUG-FIXTURE" in result.get("diff")
    finally:
        cleanup_disposable_workspace(ws, base_dir=tmp_path)


def test_codex_adapter_requires_operator_approval(tmp_path):
    adapter = CodexAgentAdapter()
    ws = tmp_path / "dummy_ws"
    ws.mkdir()

    with pytest.raises(PermissionError):
        adapter.repair({}, workspace_path=ws, operator_approved=False)


def test_codex_adapter_gracefully_reports_missing_cli(tmp_path):
    # Non-existent binary path
    adapter = CodexAgentAdapter(codex_bin="/nonexistent/bin/codex")
    assert adapter._is_available() is False

    diag = adapter.diagnose({}, workspace_path=tmp_path)
    assert diag.get("status") == "diagnosis_skipped"
    assert diag.get("read_only") is True
    assert "not installed" in diag.get("error")
