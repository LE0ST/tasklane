"""TaskLane Issue #4: Controlled Local Demonstration for Model Remediation Agent.

Orchestrates the 5-stage controlled local demonstration workflow:
1. Alert receipt & read-only telemetry diagnosis (DeepSeekAgentAdapter.diagnose).
2. Defect injection strictly in service-owned disposable workspace & BEFORE verification (HTTP 500)
   executed STRICTLY INSIDE isolated worker container (Zero host candidate execution).
3. Candidate diff proposal & isolated worker evaluation:
   - Evaluated in ephemeral container (tasklane-isolated-worker:local).
   - In-process pytest and SQLite persistence reported with explicit advisory limitations.
   - Automatic patch application is strictly DISABLED by policy (workspace target file remains untouched).
   - Generates remediation proposal with cryptographic file & diff hashes.
4. Explicit human review and approval:
   - Operator inspects proposed diff and creates cryptographically bound approval ticket.
   - Approval ticket verifies workspace path, original file SHA-256, and diff SHA-256.
   - Denies by default; patch applied ONLY after explicit confirmation.
   - Checkout root and production are NEVER modified.
5. AFTER verification & persistence check executed STRICTLY INSIDE isolated worker container:
   - Urgent task creation succeeds with HTTP 201.
   - Database persistence confirmed in SQLite.
"""
import difflib
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

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
    BUG_FIXTURE_MARKER_END,
    BUG_FIXTURE_MARKER_START,
    get_target_file,
    has_task_creation_defect,
    inject_task_creation_defect,
)
from isolated_worker import (
    IsolatedWorkerController,
    IsolatedWorkerSession,
)
from sandbox import (
    cleanup_disposable_workspace,
    create_disposable_workspace,
    is_active_workspace_registration,
)

MODE_OFFLINE_SIMULATED = "offline_simulated"
MODE_LIVE_PROVIDER = "live_provider"


class HumanReviewRejectedError(PermissionError):
    """Raised when an automated or unapproved attempt is made to apply a model patch."""
    pass


def _reject_workspace_links(path: Path) -> None:
    """Reject links in the supplied workspace path, including Windows junctions."""
    for component in (path.absolute(), *path.absolute().parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise HumanReviewRejectedError("Workspace path contains a link")


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
            if BUG_FIXTURE_MARKER_END in line or "# --- ONCALL-BUG-FIXTURE-END ---" in line:
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


def create_remediation_proposal(
    incident_id: str,
    workspace_path: Path,
    mode: str = MODE_OFFLINE_SIMULATED,
    api_key: Optional[str] = None,
    simulated_diff: Optional[str] = None,
    incident_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a candidate remediation proposal and advisory worker evaluation.
    Target file in the workspace remains untouched.
    """
    if mode != MODE_OFFLINE_SIMULATED:
        raise PermissionError("Live provider remediation blocked")
    _reject_workspace_links(Path(workspace_path))
    if not is_active_workspace_registration(incident_id, Path(workspace_path)):
        raise PermissionError("Active workspace registration required")
    safe_ws = validate_safe_workspace_path(workspace_path, required_incident_id=incident_id)
    repo_root = Path(__file__).resolve().parents[1]
    if safe_ws == repo_root or repo_root in safe_ws.parents:
        raise PermissionError(f"Security violation: Cannot propose remediation for repository root: {safe_ws}")

    target_file = validate_safe_target_file(get_target_file(safe_ws), safe_ws)
    original_bytes = target_file.read_bytes()
    original_file_sha256 = hashlib.sha256(original_bytes).hexdigest()

    # In MODE_OFFLINE_SIMULATED:
    diff_text = simulated_diff or generate_synthetic_remediation_diff(original_bytes.decode("utf-8"))
    diff_sha256 = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()

    # Evaluate in isolated worker via DeepSeekAgentAdapter with write_capable_enabled=True
    adapter = DeepSeekAgentAdapter(api_key="offline-placeholder")
    adapter.write_capable_enabled = True

    ctx = incident_context or {
        "alertname": EXPECTED_ALERT_NAME,
        "trace_id": f"trace-{incident_id}",
    }

    from unittest.mock import patch

    with patch.object(adapter, "_call_api", return_value=(f"```diff\n{diff_text}\n```", None)):
        repair_result = adapter.repair(
            ctx,
            workspace_path=safe_ws,
            operator_approved=True,
        )

    # No provider or model was executed in the simulated path.
    repair_result["model_executed"] = False
    repair_result["execution_mode"] = MODE_OFFLINE_SIMULATED

    # Confirm workspace target file was untouched
    assert target_file.read_bytes() == original_bytes, "Workspace target file was illegally modified during proposal!"

    return {
        "proposal_id": f"prop-{uuid.uuid4().hex[:8]}",
        "incident_id": incident_id,
        "workspace_path": str(safe_ws),
        "mode": mode,
        "original_file_sha256": original_file_sha256,
        "diff_sha256": diff_sha256,
        "diff": diff_text,
        "status": "proposal_generated",
        "advisory_pytest": repair_result.get("pytest_verification"),
        "advisory_persistence": repair_result.get("persistence_verification"),
        "tests_passed_advisory": repair_result.get("tests_passed_advisory", False),
        "repair_result": repair_result,
    }


def create_approval_ticket(
    proposal: Dict[str, Any],
    confirmed: bool = False,
    reviewer: str = "oncall-operator",
) -> Dict[str, Any]:
    """Create a cryptographically bound approval ticket for a proposed remediation."""
    return {
        "proposal_id": proposal.get("proposal_id"),
        "incident_id": proposal.get("incident_id"),
        "expected_workspace_path": proposal.get("workspace_path"),
        "expected_original_file_sha256": proposal.get("original_file_sha256"),
        "expected_diff_sha256": proposal.get("diff_sha256"),
        "confirmed": confirmed is True,
        "reviewer": reviewer,
    }


def apply_human_reviewed_patch(
    workspace_path: Path,
    diff_text: str,
    approval_ticket: Optional[Dict[str, Any]] = None,
    human_approved: Optional[bool] = None,
) -> Dict[str, Any]:
    """Apply a model-generated patch strictly to a disposable workspace after explicit human review.

    SAFETY CONSTRAINTS (Sol Audit):
    1. Deny by default: Patch application requires an explicit approval ticket with confirmed=True.
    2. Cryptographic binding:
       - expected_workspace_path must match workspace_path.
       - expected_original_file_sha256 must match the current target file hash (rejects concurrent tampering).
       - expected_diff_sha256 must match the diff_text hash (rejects diff tampering).
    3. Target containment: Can only target backend/main.py within a registered disposable workspace.
    4. Repo root protection: Repository root and production environments are strictly protected and never touched.
    """
    repo_root = Path(__file__).resolve().parents[1]
    _reject_workspace_links(Path(workspace_path))
    safe_ws = Path(workspace_path).resolve()
    if safe_ws == repo_root or repo_root in safe_ws.parents:
        raise PermissionError(
            f"Security violation: Cannot apply patch to main repository root: {safe_ws}"
        )

    # Check human approval / ticket
    if approval_ticket is None:
        if human_approved is not True:
            raise HumanReviewRejectedError(
                "Security violation: Patch application rejected. Explicit human review and approval required."
            )
        raise HumanReviewRejectedError(
            "Security violation: Approval ticket required. Remediation must be cryptographically bound to workspace and hashes."
        )

    if not isinstance(approval_ticket, dict) or approval_ticket.get("confirmed") is not True:
        raise HumanReviewRejectedError(
            "Security violation: Patch application rejected. Approval ticket was not explicitly confirmed."
        )

    # Validate workspace path binding
    ticket_ws_str = approval_ticket.get("expected_workspace_path", "")
    if not ticket_ws_str or Path(ticket_ws_str).resolve() != safe_ws:
        raise HumanReviewRejectedError(
            f"Security violation: Workspace path mismatch. Ticket bound to {ticket_ws_str}, but received {safe_ws}"
        )

    incident_id = approval_ticket.get("incident_id")
    if not isinstance(incident_id, str) or not incident_id:
        raise HumanReviewRejectedError("Incident registration required")
    if not is_active_workspace_registration(incident_id, Path(workspace_path)):
        raise HumanReviewRejectedError("Active workspace registration required")
    validated_ws = validate_safe_workspace_path(Path(workspace_path), required_incident_id=incident_id)
    raw_target = get_target_file(validated_ws)
    target_file = validate_safe_target_file(raw_target, validated_ws)

    # Validate original file hash
    current_bytes = target_file.read_bytes()
    current_hash = hashlib.sha256(current_bytes).hexdigest()
    if current_hash != approval_ticket.get("expected_original_file_sha256"):
        raise HumanReviewRejectedError(
            "Security violation: Target file SHA-256 mismatch. The workspace was modified concurrently."
        )

    # Validate diff hash
    actual_diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    if actual_diff_hash != approval_ticket.get("expected_diff_sha256"):
        raise HumanReviewRejectedError(
            "Security violation: Diff SHA-256 mismatch. The proposed diff does not match the approval ticket."
        )

    is_valid, err = validate_unified_diff(diff_text, allowed_rel_path="backend/main.py")
    if not is_valid:
        raise ValueError(f"Invalid unified diff: {err}")

    current_content = current_bytes.decode("utf-8")
    repaired_content, applied = apply_unified_diff(current_content, diff_text, allowed_rel_path="backend/main.py")
    if not applied:
        raise RuntimeError("Failed to apply unified diff to target file")

    # Recheck immediately before replacement; portable APIs cannot make this a CAS.
    _reject_workspace_links(Path(workspace_path))
    if not is_active_workspace_registration(incident_id, validated_ws):
        raise HumanReviewRejectedError("Workspace registration changed before write")
    validate_safe_workspace_path(Path(workspace_path), required_incident_id=incident_id)
    validate_safe_target_file(get_target_file(validated_ws), validated_ws)
    if hashlib.sha256(target_file.read_bytes()).hexdigest() != current_hash:
        raise HumanReviewRejectedError("Target changed before write")
    atomic_write_file(target_file, repaired_content)

    return {
        "status": "applied",
        "target": str(target_file),
        "diff_bytes": len(diff_text),
        "approval_ticket": approval_ticket,
        "human_approved": True,
    }


def run_controlled_model_drill(
    incident_id: Optional[str] = None,
    human_approved: bool = False,
    review_callback: Optional[Callable[[Dict[str, Any]], bool]] = None,
    simulated_diff: Optional[str] = None,
    api_key: Optional[str] = None,
    mode: str = MODE_OFFLINE_SIMULATED,
    disposable_parent: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute the full 5-stage controlled local demonstration for DeepSeekAgentAdapter.

    ZERO HOST CANDIDATE EXECUTION GUARANTEE:
    Both BEFORE (HTTP 500) and AFTER (HTTP 201) verifications execute strictly inside the
    isolated worker container (tasklane-isolated-worker:local) with --network none and --read-only root.
    Candidate code is NEVER imported or executed directly on the host.

    Stages:
    1. Alert Receipt & Read-Only Telemetry Diagnosis (read_only=True, no modifications).
    2. Defect Injection & BEFORE Container Verification (normal=201, urgent=500 in worker).
    3. Model Diff Proposal & Isolated Worker Staging Evaluation:
       - Status reported as automatic_patch_application_disabled.
       - Target file remains defective and untouched.
       - Proposal returned with original_file_sha256 and diff_sha256.
    4. Explicit Human Review:
       - Operator reviews proposed diff and generates cryptographic approval ticket.
       - A review callback receives the diff before an explicit True can authorize application.
    5. AFTER Container Verification & Persistence Check:
       - Verified inside isolated worker container: normal=201, urgent=201, db_verified=True.
       - Main repository root checkout verified completely unmodified.
    """
    if human_approved is not False:
        raise PermissionError("Pre-approval is not accepted; review the proposal first")
    if mode != MODE_OFFLINE_SIMULATED:
        raise PermissionError(
            "Live provider remediation blocked: autonomous write-capable execution not authorized for live provider"
        )

    inc_id = incident_id or f"drill-model-{uuid.uuid4().hex[:8]}"
    repo_root = Path(__file__).resolve().parents[1]
    repo_main_py = repo_root / "backend" / "main.py"
    repo_root_mtime_before = repo_main_py.stat().st_mtime if repo_main_py.exists() else None

    # Initialize disposable workspace
    ws = create_disposable_workspace(repo_root, inc_id)

    try:
        # Stage 1: Alert Receipt & Read-Only Telemetry Diagnosis
        diag_result = {
            "adapter": "offline_simulated", "model_executed": False,
            "read_only": True, "status": "diagnosed",
            "analysis": "Simulated diagnosis of the injected urgent task failure.",
        }
        incident_context = {"alertname": EXPECTED_ALERT_NAME, "trace_id": f"trace-{inc_id}"}

        # Stage 2: Inject defect & BEFORE verification STRICTLY INSIDE ISOLATED WORKER CONTAINER
        inject_task_creation_defect(ws)
        assert has_task_creation_defect(ws), "Failed to inject defect in disposable workspace"

        with IsolatedWorkerSession.create(ws) as before_session:
            _, before_http, _ = IsolatedWorkerController.run_isolated_functional_verification(before_session)

        assert before_http.get("normal_status") == 201, "Normal task should return 201 before repair"
        assert before_http.get("urgent_status") == 500, "Urgent task must fail with 500 before repair"

        # Stage 3: Remediation Proposal & Isolated Worker Evaluation
        proposal = create_remediation_proposal(
            incident_id=inc_id,
            workspace_path=ws,
            mode=mode,
            api_key=api_key,
            simulated_diff=simulated_diff,
            incident_context=incident_context,
        )

        repair_result = proposal.get("repair_result", {})
        # Verify policy constraints: automatic application was strictly withheld
        assert repair_result.get("status") == "automatic_patch_application_disabled"
        assert repair_result.get("patch_applied") is False
        assert repair_result.get("automatic_patch_application_enabled") is False
        assert repair_result.get("independent_validation") is False

        # Confirm workspace target file was untouched by adapter
        assert has_task_creation_defect(ws), "Target file must remain defective after adapter proposal"

        # Stage 4: Explicit Human Review & Approval Ticket
        patch_applied_by_human = False
        human_review_result = {}

        # The callback receives the full diff before it can return an explicit decision.
        decision = review_callback(dict(proposal)) if review_callback is not None else False
        if decision is True:
            ticket = create_approval_ticket(proposal, confirmed=decision)
            human_review_result = apply_human_reviewed_patch(
                workspace_path=ws,
                diff_text=proposal["diff"],
                approval_ticket=ticket,
            )
            patch_applied_by_human = True
        else:
            ticket = create_approval_ticket(proposal, confirmed=False)
            human_review_result = {
                "status": "pending_human_review",
                "human_approved": False,
                "approval_ticket": ticket,
                "detail": "Operator did not approve patch; disposable workspace preserved in failing state.",
            }

        # Stage 5: AFTER Verification STRICTLY INSIDE ISOLATED WORKER CONTAINER
        if patch_applied_by_human:
            with IsolatedWorkerSession.create(ws) as after_session:
                func_passed, after_http, _ = IsolatedWorkerController.run_isolated_functional_verification(after_session)

            overall_success = func_passed is True
            after_http = {
                **after_http,
                "status": "passed" if overall_success else "failed",
                "functional_passed": overall_success,
            }
            if not overall_success:
                after_http["detail"] = (
                    "AFTER worker verification failed; its exit status or functional checks "
                    "did not pass. Parsed observations alone do not establish recovery."
                )
        else:
            after_http = {"status": "not_run"}
            overall_success = False

        # Verification of main checkout isolation: repo root must never have been modified
        repo_root_mtime_after = repo_main_py.stat().st_mtime if repo_main_py.exists() else None
        assert repo_root_mtime_before == repo_root_mtime_after, "Main checkout root was modified!"

        return {
            "incident_id": inc_id,
            "success": overall_success,
            "execution_mode": mode,
            "stages": {
                "stage_1_diagnosis": diag_result,
                "stage_2_before_http": before_http,
                "stage_3_model_proposal": repair_result,
                "stage_4_human_review": human_review_result,
                "stage_5_after_http": after_http,
            },
            "disposable_workspace": str(ws),
            "proposal": {
                "proposal_id": proposal.get("proposal_id"),
                "diff": proposal.get("diff"),
                "original_file_sha256": proposal.get("original_file_sha256"),
                "diff_sha256": proposal.get("diff_sha256"),
            },
            "main_checkout_unmodified": True,
            "limitations": {
                "verifier_limitations": (
                    "In-process pytest shares Python runtime with candidate code (advisory only). "
                    "SQLite persistence in tmpfs shares UID 10001 with candidate process."
                ),
                "http_verification_scope": (
                    "HTTP checks run over loopback inside the isolated worker container with --network none. "
                    "A separate verifier process checks API response codes; shared container privileges "
                    "do not establish an independent security boundary."
                ),
                "sqlite_persistence_scope": (
                    "SQLite verification directly inspects the database file on disk (/tmp/tasklane.db) "
                    "to confirm physical persistence of task records beyond superficial HTTP responses. "
                    "Limited by shared container user namespace (UID 10001) between candidate and verifier."
                ),
                "pytest_advisory_scope": (
                    "Pytest execution provides rapid regression feedback against the backend test suite. "
                    "Because candidate code is imported into the test process, results are strictly "
                    "advisory and non-authoritative; automatic patch application remains disabled."
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
    def review(proposal):
        print("Proposed diff:\n" + proposal["diff"])
        print("Workspace:", proposal["workspace_path"])
        print("Original SHA-256:", proposal["original_file_sha256"])
        print("Diff SHA-256:", proposal["diff_sha256"])
        return input("Apply this diff to the disposable workspace? Type YES: ") == "YES"

    res = run_controlled_model_drill(review_callback=review)
    print("Drill finished:")
    print(json.dumps(res, indent=2))
    sys.exit(0 if res.get("success") else 1)
