"""Offline and integration tests for the Controlled Model Remediation Drill.

Verifies:
1. Full 5-stage controlled drill workflow with zero host candidate execution.
2. Proposal generation with SHA-256 cryptographic hashes (original file and diff).
3. Cryptographically bound human approval tickets (denial by default, hash and path validation).
4. Explicit blocking of live provider mode pending authorization.
5. Selective container recovery: protection of active sessions, living creator PIDs, and non-worker containers.
"""
import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from model_drill import (
    HumanReviewRejectedError,
    MODE_LIVE_PROVIDER,
    MODE_OFFLINE_SIMULATED,
    apply_human_reviewed_patch,
    create_approval_ticket,
    create_remediation_proposal,
    generate_synthetic_remediation_diff,
    run_controlled_model_drill,
)
from isolated_worker import (
    IsolatedWorkerSession,
    _ACTIVE_SESSIONS,
    _ACTIVE_SESSIONS_LOCK,
    _PENDING_CONTAINERS,
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


def test_controlled_model_drill_end_to_end_offline(monkeypatch):
    """Verify the full 5-stage controlled model drill lifecycle:
    1. Alert receipt & read-only diagnosis.
    2. Defect injection & BEFORE container verification (HTTP 500 on urgent task).
    3. Model diff proposal & isolated worker staging evaluation (automatic application withheld).
    4. Explicit human review and approval ticket validation.
    5. AFTER container verification (HTTP 201 on urgent task & SQLite persistence confirmed).
    Main checkout remains strictly unmodified. Zero host candidate execution.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unused-test-key")
    def reject_provider(*args, **kwargs):
        raise AssertionError("offline drill attempted provider access")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", reject_provider)
    reviewed = []
    def simulated_review(proposal):
        # This is a test double, not evidence of human intervention.
        assert proposal["diff"]
        assert has_task_creation_defect(Path(proposal["workspace_path"]))
        reviewed.append(proposal["diff"])
        return True

    res = run_controlled_model_drill(
        incident_id="test-model-drill-e2e",
        review_callback=simulated_review,
        api_key="unused-argument-key",
    )
    assert reviewed


    assert res.get("success") is True
    assert res.get("execution_mode") == MODE_OFFLINE_SIMULATED
    assert res.get("main_checkout_unmodified") is True

    stages = res.get("stages", {})

    # Stage 1: Diagnosis
    st1 = stages.get("stage_1_diagnosis", {})
    assert st1.get("read_only") is True
    assert st1.get("status") == "diagnosed"

    # Stage 2: BEFORE HTTP inside container
    st2 = stages.get("stage_2_before_http", {})
    assert st2.get("normal_status") == 201
    assert st2.get("urgent_status") == 500

    # Stage 3: Model Proposal (Isolated Worker staging)
    st3 = stages.get("stage_3_model_proposal", {})
    assert st3.get("status") == "automatic_patch_application_disabled"
    assert st3.get("patch_applied") is False
    assert st3.get("independent_validation") is False
    assert st3.get("tests_passed") is False
    assert st3.get("tests_passed_advisory") is True
    assert st3.get("pytest_verification", {}).get("status") == "advisory_passed"
    assert st3.get("pytest_verification", {}).get("trust_level") == "advisory_untrusted"
    assert st3.get("persistence_verification", {}).get("status") == "db_verified"

    # Cryptographic hashes on proposal
    prop = res.get("proposal", {})
    assert len(prop.get("original_file_sha256", "")) == 64
    assert len(prop.get("diff_sha256", "")) == 64

    # Stage 4: Human Review
    st4 = stages.get("stage_4_human_review", {})
    assert st4.get("status") == "applied"
    assert st4.get("human_approved") is True

    # Stage 5: AFTER HTTP inside container
    st5 = stages.get("stage_5_after_http", {})
    assert st5.get("normal_status") == 201
    assert st5.get("urgent_status") == 201
    assert st5.get("urgent_persisted") is True

    # Explicit limitations disclosure
    limits = res.get("limitations", {})
    assert "advisory" in limits.get("verifier_limitations", "").lower()
    assert "loopback" in limits.get("http_verification_scope", "").lower()
    assert "persistence" in limits.get("sqlite_persistence_scope", "").lower()
    assert "advisory" in limits.get("pytest_advisory_scope", "").lower()
    assert "authorization gate" in limits.get("human_approval_limitations", "").lower()


def test_controlled_model_drill_withheld_when_human_approval_denied():
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
    assert st5.get("status") == "not_run"


def test_approval_rejected_if_not_confirmed():
    """Verify that approval ticket without confirmed=True is rejected by default."""
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, "test-confirm-false")
    try:
        inject_task_creation_defect(ws)
        proposal = create_remediation_proposal("test-confirm-false", ws)
        ticket = create_approval_ticket(proposal, confirmed=False)

        with pytest.raises(HumanReviewRejectedError, match="not explicitly confirmed"):
            apply_human_reviewed_patch(ws, proposal["diff"], approval_ticket=ticket)
    finally:
        cleanup_disposable_workspace(ws, "test-confirm-false")


def test_approval_rejected_if_diff_hash_mismatch():
    """Verify that tampering with the proposed diff causes ticket rejection."""
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, "test-tamper-diff")
    try:
        inject_task_creation_defect(ws)
        proposal = create_remediation_proposal("test-tamper-diff", ws)
        ticket = create_approval_ticket(proposal, confirmed=True)

        tampered_diff = proposal["diff"] + "\n# malicious comment"
        with pytest.raises(HumanReviewRejectedError, match="Diff SHA-256 mismatch"):
            apply_human_reviewed_patch(ws, tampered_diff, approval_ticket=ticket)
    finally:
        cleanup_disposable_workspace(ws, "test-tamper-diff")


def test_approval_rejected_if_original_file_modified_concurrently():
    """Verify that concurrent modification of the target file invalidates approval."""
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, "test-concurrent-mod")
    try:
        inject_task_creation_defect(ws)
        proposal = create_remediation_proposal("test-concurrent-mod", ws)
        ticket = create_approval_ticket(proposal, confirmed=True)

        # Concurrently alter target file
        target_file = get_target_file(ws)
        target_file.write_text(target_file.read_text(encoding="utf-8") + "\n# altered", encoding="utf-8")

        with pytest.raises(HumanReviewRejectedError, match="Target file SHA-256 mismatch"):
            apply_human_reviewed_patch(ws, proposal["diff"], approval_ticket=ticket)
    finally:
        cleanup_disposable_workspace(ws, "test-concurrent-mod")


def test_approval_rejected_if_workspace_path_mismatch():
    """Verify that presenting a ticket for workspace A to workspace B is rejected."""
    repo_root = Path(__file__).resolve().parents[2]
    ws1 = create_disposable_workspace(repo_root, "test-ws-1")
    ws2 = create_disposable_workspace(repo_root, "test-ws-2")
    try:
        inject_task_creation_defect(ws1)
        inject_task_creation_defect(ws2)
        proposal1 = create_remediation_proposal("test-ws-1", ws1)
        ticket1 = create_approval_ticket(proposal1, confirmed=True)

        with pytest.raises(HumanReviewRejectedError, match="Workspace path mismatch"):
            apply_human_reviewed_patch(ws2, proposal1["diff"], approval_ticket=ticket1)
    finally:
        cleanup_disposable_workspace(ws1, "test-ws-1")
        cleanup_disposable_workspace(ws2, "test-ws-2")


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


def test_live_provider_mode_explicitly_blocked():
    """Verify that MODE_LIVE_PROVIDER raises PermissionError."""
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, "test-live-mode")
    try:
        with pytest.raises(PermissionError, match="Live provider remediation blocked"):
            run_controlled_model_drill(mode=MODE_LIVE_PROVIDER)

        with pytest.raises(PermissionError, match="Live provider remediation blocked"):
            create_remediation_proposal("test-live-mode", ws, mode=MODE_LIVE_PROVIDER)
    finally:
        cleanup_disposable_workspace(ws, "test-live-mode")


def test_automatic_container_recovery_is_disabled(monkeypatch, tmp_path):
    import isolated_worker
    state_file = tmp_path / "pending.json"
    state_file.write_text("{corrupt", encoding="utf-8")
    monkeypatch.setattr(isolated_worker, "_PENDING_CONTAINERS_STATE_FILE", state_file)
    def forbidden(*args, **kwargs):
        raise AssertionError("recovery must not remove containers")
    monkeypatch.setattr(IsolatedWorkerSession, "_remove_docker_container", forbidden)
    report = IsolatedWorkerSession.recover_orphaned_containers()
    assert report["status"] == "automatic_recovery_disabled"
    assert report["unknown_count"] > 0
    assert state_file.read_text(encoding="utf-8") == "{corrupt"


@pytest.mark.parametrize("after_exit, expected_success", [(1, False), (0, True)])
def test_after_requires_successful_worker_exit(monkeypatch, tmp_path, after_exit, expected_success):
    """Exercise the real functional parser with apparent success and varied exit codes."""
    from isolated_worker import IsolatedWorkerController
    from agent_adapter import DeepSeekAgentAdapter

    session = MagicMock()
    session.staging_dir = tmp_path
    session.session_id = "test-session"
    context = MagicMock()
    context.__enter__.return_value = session
    monkeypatch.setattr(IsolatedWorkerSession, "create", lambda *args, **kwargs: context)
    monkeypatch.setattr(IsolatedWorkerController, "get_immutable_image_id", lambda: "sha256:stub")
    before = {"normal_status": 201, "urgent_status": 500}
    after = {"normal_status": 201, "urgent_status": 201, "urgent_persisted": True, "db_verified": True}
    calls = []
    def fake_container(cmd, container_name, session, timeout):
        calls.append(container_name)
        data = before if len(calls) == 1 else after
        code = 0 if len(calls) == 1 else after_exit
        return code, "TRUSTED_VERIFIER_JSON:" + json.dumps(data)
    monkeypatch.setattr(IsolatedWorkerController, "_execute_bounded_container", fake_container)
    monkeypatch.setattr(DeepSeekAgentAdapter, "repair", lambda *args, **kwargs: {
        "status": "automatic_patch_application_disabled", "patch_applied": False,
        "automatic_patch_application_enabled": False, "independent_validation": False,
    })
    result = run_controlled_model_drill(incident_id=f"after-exit-{after_exit}", review_callback=lambda proposal: True)
    assert len(calls) == 2
    assert result["success"] is expected_success
    stage = result["stages"]["stage_5_after_http"]
    assert stage["functional_passed"] is expected_success
    assert stage["status"] == ("passed" if expected_success else "failed")
    assert stage["urgent_status"] == 201
    if not expected_success:
        assert "worker" in stage["detail"].lower()


def test_after_is_not_reported_when_review_is_withheld(monkeypatch, tmp_path):
    """The drill must not claim an AFTER result when no second worker was run."""
    from isolated_worker import IsolatedWorkerController
    from agent_adapter import DeepSeekAgentAdapter

    session = MagicMock(staging_dir=tmp_path, session_id="test-session")
    context = MagicMock()
    context.__enter__.return_value = session
    monkeypatch.setattr(IsolatedWorkerSession, "create", lambda *args, **kwargs: context)
    monkeypatch.setattr(IsolatedWorkerController, "get_immutable_image_id", lambda: "sha256:stub")
    calls = []
    def fake_container(cmd, container_name, session, timeout):
        calls.append(container_name)
        return 0, 'TRUSTED_VERIFIER_JSON:{"normal_status": 201, "urgent_status": 500}'
    monkeypatch.setattr(IsolatedWorkerController, "_execute_bounded_container", fake_container)
    monkeypatch.setattr(DeepSeekAgentAdapter, "repair", lambda *args, **kwargs: {
        "status": "automatic_patch_application_disabled", "patch_applied": False,
        "automatic_patch_application_enabled": False, "independent_validation": False,
    })
    result = run_controlled_model_drill(incident_id="after-not-run")
    assert len(calls) == 1
    assert result["success"] is False
    assert result["stages"]["stage_5_after_http"] == {"status": "not_run"}
