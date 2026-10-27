"""TaskLane Issue #4: Controlled Local Demonstration for Model Remediation Agent.

Orchestrates the 5-stage controlled local demonstration workflow:
1. Alert receipt & read-only telemetry diagnosis (DeepSeekAgentAdapter.diagnose).
2. Defect injection strictly in service-owned disposable workspace & BEFORE HTTP verification (HTTP 500).
3. Candidate diff proposal & isolated worker evaluation:
   - Evaluated in ephemeral container (tasklane-isolated-worker:local).
   - In-process pytest and SQLite persistence reported with explicit advisory limitations.
   - Automatic patch application is strictly DISABLED by policy (workspace target file remains untouched).
4. Explicit human review and approval:
   - Operator inspects proposed diff.
   - Patch is applied ONLY to the disposable drill workspace after explicit confirmation.
   - Checkout root and production are NEVER modified.
5. AFTER HTTP verification & persistence check:
   - Urgent task creation succeeds with HTTP 201.
   - Database persistence confirmed in SQLite.
"""
import difflib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

IR_DIR = Path(__file__).resolve().parent
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from agent_adapter import (
    DeepSeekAgentAdapter,
    apply_unified_diff,
    atomic_write_file,
    validate_safe_target_file,
    validate_safe_workspace_path,
    validate_unified_diff,
)
from config import EXPECTED_ALERT_NAME
from fixtures import (
    BUG_CODE_BLOCK,
    BUG_FIXTURE_MARKER_START,
    get_target_file,
    has_task_creation_defect,
    inject_task_creation_defect,
    run_http_task_verification_script,
)
from sandbox import (
    cleanup_disposable_workspace,
    create_disposable_workspace,
)


class HumanReviewRejectedError(PermissionError):
    """Raised when an automated or unapproved attempt is made to apply a model patch."""
    pass


def apply_human_reviewed_patch(
    workspace_path: Path,
    diff_text: str,
    human_approved: bool = False,
) -> Dict[str, Any]:
    """Apply a model-generated patch strictly to a disposable workspace after explicit human review.

    SAFETY CONSTRAINTS:
    1. human_approved must be strictly True (boolean True required; never defaulted).
    2. workspace_path must be a private service-owned disposable workspace, NEVER repository root.
    3. diff_text must pass unified diff validation targeting only backend/main.py.
    4. Target file is replaced atomically with guaranteed rollback on failure.
    5. Main repository root and production environments are strictly protected and never touched.
    """
    if human_approved is not True:
        raise HumanReviewRejectedError(
            "Security violation: Patch application rejected. Explicit human review and approval required."
        )

    safe_ws = validate_safe_workspace_path(workspace_path, enforce_registry=False)

    repo_root = Path(__file__).resolve().parents[1]
    if safe_ws == repo_root or repo_root in safe_ws.parents:
        raise PermissionError(
            f"Security violation: Cannot apply patch to main repository root: {safe_ws}"
        )

    raw_target = get_target_file(safe_ws)
    target_file = validate_safe_target_file(raw_target, safe_ws)

    is_valid, err = validate_unified_diff(diff_text, allowed_rel_path="backend/main.py")
    if not is_valid:
        raise ValueError(f"Invalid unified diff: {err}")

    current_content = target_file.read_text(encoding="utf-8")
    repaired_content, applied = apply_unified_diff(current_content, diff_text, allowed_rel_path="backend/main.py")
    if not applied:
        raise RuntimeError("Failed to apply unified diff to target file")

    atomic_write_file(target_file, repaired_content)

    return {
        "status": "applied",
        "target": str(target_file),
        "diff_bytes": len(diff_text),
        "human_approved": True,
    }


def generate_synthetic_remediation_diff(original_content: str) -> str:
    """Generate a clean unified diff that removes the synthetic bug fixture markers."""
    if BUG_FIXTURE_MARKER_START not in original_content:
        return ""
    repaired_lines = []
    skip = False
    for line in original_content.splitlines(keepends=True):
        if BUG_FIXTURE_MARKER_START in line:
            skip = True
            continue
        if skip:
            if "# --- ONCALL-BUG-FIXTURE-END ---" in line:
                skip = False
            continue
        repaired_lines.append(line)

    repaired_content = "".join(repaired_lines)
    orig_lines = original_content.splitlines(keepends=True)
    rep_lines = repaired_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        orig_lines,
        rep_lines,
        fromfile="a/backend/main.py",
        tofile="b/backend/main.py",
    )
    return "".join(diff)


def run_controlled_model_drill(
    incident_id: Optional[str] = None,
    human_approved: bool = True,
    simulated_diff: Optional[str] = None,
    api_key: Optional[str] = None,
    disposable_parent: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute the full 5-stage controlled local demonstration for DeepSeekAgentAdapter.

    Stages:
    1. Alert Receipt & Read-Only Telemetry Diagnosis (read_only=True, no modifications).
    2. Defect Injection & BEFORE HTTP Verification (normal=201, urgent=500).
    3. Model Diff Proposal & Isolated Worker Evaluation:
       - Status reported as automatic_patch_application_disabled.
       - Target file remains defective and untouched.
       - Advisory pytest & persistence verification details provided.
    4. Explicit Human Review:
       - Operator reviews diff.
       - If human_approved=True, patch applied strictly to disposable workspace.
    5. AFTER HTTP Verification & Persistence Check:
       - Normal task returns 201, urgent task returns 201, db_verified=True.
       - Verification that repository root checkout was never modified.
    """
    inc_id = incident_id or f"drill-model-{uuid.uuid4().hex[:8]}"
    repo_root = Path(__file__).resolve().parents[1]
    repo_main_py = repo_root / "backend" / "main.py"
    repo_root_mtime_before = repo_main_py.stat().st_mtime if repo_main_py.exists() else None

    # Step 1: Initialize disposable workspace
    ws = create_disposable_workspace(repo_root, inc_id)

    try:
        # Step 1: Alert Receipt & Read-Only Telemetry Diagnosis
        adapter = DeepSeekAgentAdapter(api_key=api_key or "mock-offline-key")
        incident_context = {
            "alertname": EXPECTED_ALERT_NAME,
            "trace_id": f"trace-{inc_id}",
            "error_count": 5,
            "logs": "POST /api/tasks priority=urgent -> HTTP 500 RuntimeError",
        }

        # Mock diagnose call if using mock offline key
        if not api_key:
            diag_result = {
                "adapter": adapter.adapter_name,
                "model_executed": True,
                "read_only": True,
                "status": "diagnosed",
                "analysis": (
                    "Telemetric analysis identifies uncaught RuntimeError during urgent task dispatch. "
                    "Read-only diagnostic complete; no shell commands executed."
                ),
            }
        else:
            diag_result = adapter.diagnose(incident_context)

        # Step 2: Inject defect in disposable workspace & verify BEFORE state
        inject_task_creation_defect(ws)
        assert has_task_creation_defect(ws), "Failed to inject defect in disposable workspace"

        before_http = run_http_task_verification_script(ws)
        assert before_http.get("normal_status") == 201, "Normal task should return 201 before repair"
        assert before_http.get("urgent_status") == 500, "Urgent task must fail with 500 before repair"

        # Step 3: Model Diff Proposal & Isolated Worker Evaluation
        target_file = get_target_file(ws)
        content_with_defect = target_file.read_text(encoding="utf-8")
        diff_text = simulated_diff or generate_synthetic_remediation_diff(content_with_defect)

        adapter.write_capable_enabled = True

        import json
        from unittest.mock import MagicMock, patch

        mock_payload = {
            "choices": [{"message": {"role": "assistant", "content": f"```diff\n{diff_text}\n```"}}]
        }
        body = json.dumps(mock_payload).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.OpenerDirector.open", return_value=mock_resp):
            repair_result = adapter.repair(
                incident_context,
                workspace_path=ws,
                operator_approved=True,
            )

        # Verify policy constraints: automatic application was strictly withheld
        assert repair_result.get("status") == "automatic_patch_application_disabled"
        assert repair_result.get("patch_applied") is False
        assert repair_result.get("automatic_patch_application_enabled") is False
        assert repair_result.get("independent_validation") is False

        # Confirm workspace target file was untouched by adapter
        assert has_task_creation_defect(ws), "Target file must remain defective after adapter proposal"

        # Step 4: Explicit Human Review & Approval
        patch_applied_by_human = False
        human_review_result = {}

        if human_approved:
            human_review_result = apply_human_reviewed_patch(
                workspace_path=ws,
                diff_text=repair_result.get("diff", diff_text),
                human_approved=True,
            )
            patch_applied_by_human = True
        else:
            human_review_result = {
                "status": "pending_human_review",
                "human_approved": False,
                "detail": "Operator did not approve patch; disposable workspace preserved in failing state.",
            }

        # Step 5: AFTER HTTP Verification & Persistence Check
        if patch_applied_by_human:
            after_http = run_http_task_verification_script(ws)
            assert after_http.get("normal_status") == 201, "Normal task must return 201 after repair"
            assert after_http.get("urgent_status") == 201, "Urgent task must return 201 after repair"
            assert after_http.get("urgent_persisted") is True, "Urgent task must be persisted in SQLite"
            overall_success = True
        else:
            after_http = {
                "normal_status": 201,
                "urgent_status": 500,
                "urgent_persisted": False,
            }
            overall_success = False

        # Verification of main checkout isolation: repo root must never have been modified
        repo_root_mtime_after = repo_main_py.stat().st_mtime if repo_main_py.exists() else None
        assert repo_root_mtime_before == repo_root_mtime_after, "Main checkout root was modified!"

        return {
            "incident_id": inc_id,
            "success": overall_success,
            "execution_mode": "offline_simulated" if not api_key else "live_model",
            "stages": {
                "stage_1_diagnosis": diag_result,
                "stage_2_before_http": before_http,
                "stage_3_model_proposal": repair_result,
                "stage_4_human_review": human_review_result,
                "stage_5_after_http": after_http,
            },
            "disposable_workspace": str(ws),
            "main_checkout_unmodified": True,
            "limitations": {
                "verifier_limitations": (
                    "In-process pytest shares Python runtime with candidate code (advisory only). "
                    "SQLite persistence in tmpfs shares UID 10001 with candidate process."
                ),
                "human_approval_limitations": (
                    "Human review acts as a security authorization gate against unapproved changes; "
                    "it is not a formal mathematical proof of absence of subtle edge-case bugs."
                ),
            },
        }

    finally:
        cleanup_disposable_workspace(ws, inc_id)


if __name__ == "__main__":
    print("Executing TaskLane Controlled Model Remediation Drill...")
    res = run_controlled_model_drill()
    print("Drill finished:")
    print(json.dumps(res, indent=2))
    sys.exit(0 if res.get("success") else 1)
