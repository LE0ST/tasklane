"""Offline unit and integration tests for the Controlled Model Remediation Drill."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from model_drill import (
    HumanReviewRejectedError,
    apply_human_reviewed_patch,
    generate_synthetic_remediation_diff,
    run_controlled_model_drill,
)
from isolated_worker import (
    IsolatedWorkerSession,
    _PENDING_CONTAINERS_STATE_FILE,
    _sync_pending_containers_to_disk,
)
from sandbox import (
    SecurityViolation,
    create_disposable_workspace,
    cleanup_disposable_workspace,
)
from fixtures import (
    inject_task_creation_defect,
    has_task_creation_defect,
    get_target_file,
)


def test_controlled_model_drill_end_to_end_offline(tmp_path):
    """Verify the full 5-stage controlled model drill lifecycle offline:
    1. Alert receipt & read-only diagnosis.
    2. Defect injection & BEFORE verification (HTTP 500 on urgent task).
    3. Model diff proposal & isolated worker evaluation (automatic application withheld).
    4. Explicit human review and approval.
    5. AFTER verification (HTTP 201 on urgent task & SQLite persistence confirmed).
    Main checkout remains strictly unmodified.
    """
    res = run_controlled_model_drill(
        incident_id="test-model-drill-e2e",
        human_approved=True,
    )

    assert res.get("success") is True
    assert res.get("execution_mode") == "offline_simulated"
    assert res.get("main_checkout_unmodified") is True

    stages = res.get("stages", {})

    # Stage 1: Diagnosis
    st1 = stages.get("stage_1_diagnosis", {})
    assert st1.get("read_only") is True
    assert st1.get("status") == "diagnosed"

    # Stage 2: BEFORE HTTP
    st2 = stages.get("stage_2_before_http", {})
    assert st2.get("normal_status") == 201
    assert st2.get("urgent_status") == 500

    # Stage 3: Model Proposal (Isolated Worker)
    st3 = stages.get("stage_3_model_proposal", {})
    assert st3.get("status") == "automatic_patch_application_disabled"
    assert st3.get("patch_applied") is False
    assert st3.get("independent_validation") is False
    assert st3.get("pytest_verification", {}).get("status") == "advisory_passed"
    assert st3.get("pytest_verification", {}).get("trust_level") == "advisory_untrusted"
    assert st3.get("persistence_verification", {}).get("status") == "db_verified"

    # Stage 4: Human Review
    st4 = stages.get("stage_4_human_review", {})
    assert st4.get("status") == "applied"
    assert st4.get("human_approved") is True

    # Stage 5: AFTER HTTP
    st5 = stages.get("stage_5_after_http", {})
    assert st5.get("normal_status") == 201
    assert st5.get("urgent_status") == 201
    assert st5.get("urgent_persisted") is True

    # Explicit limitations disclosure
    limits = res.get("limitations", {})
    assert "advisory" in limits.get("verifier_limitations", "").lower()
    assert "authorization gate" in limits.get("human_approval_limitations", "").lower()


def test_controlled_model_drill_withheld_when_human_approval_denied(tmp_path):
    """Verify that when human approval is denied (human_approved=False):
    - Proposed diff is NOT applied to the workspace.
    - Workspace remains in failing baseline state (HTTP 500).
    - Overall drill result is False.
    """
    res = run_controlled_model_drill(
        incident_id="test-model-drill-rejected",
        human_approved=False,
    )

    assert res.get("success") is False

    stages = res.get("stages", {})
    st4 = stages.get("stage_4_human_review", {})
    assert st4.get("status") == "pending_human_review"
    assert st4.get("human_approved") is False

    st5 = stages.get("stage_5_after_http", {})
    assert st5.get("urgent_status") == 500
    assert st5.get("urgent_persisted") is False


def test_apply_human_reviewed_patch_blocks_repository_root():
    """Verify that apply_human_reviewed_patch strictly refuses to apply patches
    to the repository root checkout or outside the disposable workspace.
    """
    repo_root = Path(__file__).resolve().parents[2]
    sample_diff = "--- a/backend/main.py\n+++ b/backend/main.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"

    with pytest.raises((PermissionError, SecurityViolation)):
        apply_human_reviewed_patch(repo_root, sample_diff, human_approved=True)


def test_apply_human_reviewed_patch_requires_explicit_boolean_true():
    """Verify that apply_human_reviewed_patch raises HumanReviewRejectedError
    when human_approved is False or not strictly boolean True.
    """
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, "test-human-check")
    try:
        sample_diff = "--- a/backend/main.py\n+++ b/backend/main.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
        with pytest.raises(HumanReviewRejectedError, match="Explicit human review"):
            apply_human_reviewed_patch(ws, sample_diff, human_approved=False)
    finally:
        cleanup_disposable_workspace(ws, "test-human-check")


def test_recover_orphaned_containers_with_persistent_state_file(monkeypatch, tmp_path):
    """Verify that IsolatedWorkerSession.recover_orphaned_containers reads
    the on-disk state file and successfully cleans up recorded orphaned containers.
    """
    import subprocess
    fake_state_file = tmp_path / "test_pending.json"
    fake_containers = ["tasklane-orphaned-1", "tasklane-orphaned-2"]
    fake_state_file.write_text(json.dumps(fake_containers), encoding="utf-8")

    import isolated_worker
    monkeypatch.setattr(isolated_worker, "_PENDING_CONTAINERS_STATE_FILE", fake_state_file)

    call_records = []

    def mock_run(cmd, **kwargs):
        call_records.append(cmd)
        if "ps" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="tasklane-orphaned-3\n", stderr="")
        if "rm" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    report = IsolatedWorkerSession.recover_orphaned_containers()
    assert report.get("status") == "all_cleaned"
    assert report.get("cleaned_count") >= 2
    assert "docker rm -f" in report.get("manual_recovery_command")
