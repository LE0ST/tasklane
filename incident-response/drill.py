"""TaskLane Issue #4: Autonomous On-Call Incident Drill Orchestrator.

Exercises the complete 7-stage remediation lifecycle:
1. Responder Health Verification (GET /health).
2. Disposable Workspace Provisioning & Defect Injection.
3. BEFORE Verification:
   - Normal task creation succeeds (HTTP 201).
   - Urgent task creation fails (HTTP 500).
4. Webhook Alert Receipt & Read-Only Diagnosis (write_authorized: false).
5. Explicit Operator Authorization via Local Operator CLI (single-use token).
6. Code Diff Generation & Isolated Unit Test Execution.
7. AFTER Verification:
   - Urgent task creation succeeds (HTTP 201).
   - Repaired urgent task confirmed persisted in local database.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict

from config import EXPECTED_ALERT_NAME, RESPONDER_HOST, RESPONDER_PORT
from fixtures import (
    has_task_creation_defect,
    inject_task_creation_defect,
    run_http_task_verification_script,
)
from operator_cli import authorize_repair
from sandbox import (
    cleanup_disposable_workspace,
    create_disposable_workspace,
    get_registered_workspace,
)

BASE_URL = f"http://127.0.0.1:{RESPONDER_PORT}"


def get_json(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str] = None) -> Dict[str, Any]:
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_incident_drill() -> bool:
    print("=" * 60)
    print("TASKLANE ISSUE #4: ON-CALL REMEDIATION DRILL")
    print("=" * 60)

    # 1. Health check
    print("\n[Stage 1/7] Checking On-Call Responder health...")
    try:
        health = get_json(f"{BASE_URL}/health")
        assert health.get("status") == "healthy", f"Unexpected health: {health}"
        print(f"PASS: Responder healthy on port {RESPONDER_PORT}")

        # Clear any ongoing incident state
        st_data = get_json(f"{BASE_URL}/status")
        if st_data.get("current_status") in ("diagnosing", "diagnosed", "repairing"):
            post_json(f"{BASE_URL}/alerts", {
                "status": "resolved",
                "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
            })
    except Exception as exc:
        print(f"FAIL: Responder unreachable: {exc}")
        return False

    # 2. Simulate firing alert
    print("\n[Stage 2/7] Sending simulated Grafana firing webhook...")
    alert_payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": EXPECTED_ALERT_NAME,
                    "service": "tasklane-api",
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "TaskLane local API task operation returned HTTP 5xx",
                },
            }
        ],
    }

    try:
        diag_resp = post_json(f"{BASE_URL}/alerts", alert_payload)
        incident_id = diag_resp.get("incident_id")
        assert diag_resp.get("status") == "diagnosed", f"Expected diagnosed, got {diag_resp}"
        assert diag_resp.get("read_only") is True, "Must be read-only"
        assert diag_resp.get("write_authorized") is False, "Write must not be authorized"
        print(f"PASS: Alert received. Incident ID: {incident_id}")
        print(f"      Status: {diag_resp.get('status')} (read_only=True, write_authorized=False)")
    except Exception as exc:
        print(f"FAIL: Alert webhook failed: {exc}")
        return False

    # 3. Inject deliberate defect in the service-registered workspace
    print("\n[Stage 3/7] Injecting defect in service-registered workspace...")
    workspace_path = get_registered_workspace(incident_id)
    assert workspace_path is not None, f"Workspace not found for incident {incident_id}"
    inject_task_creation_defect(workspace_path)
    assert has_task_creation_defect(workspace_path), "Defect not injected"
    print(f"PASS: Injected narrow urgent-task regression in {workspace_path.name}")

    # 4. BEFORE Verification: real HTTP behavior
    print("\n[Stage 4/7] BEFORE repair: Verifying HTTP behavior...")
    before_check = run_http_task_verification_script(workspace_path)
    print(f"      Normal task status: {before_check.get('normal_status')} (expected 201)")
    print(f"      Urgent task status: {before_check.get('urgent_status')} (expected 500)")
    assert before_check.get("normal_status") == 201, "Normal task must return 201"
    assert before_check.get("urgent_status") == 500, "Urgent task must fail with 500 before repair"
    print("PASS: Verified failing baseline (ordinary=201, urgent=500).")

    # 5. Authorize remediation via operator CLI
    print("\n[Stage 5/7] Authorizing repair via local operator CLI (single-use token)...")
    repair_resp = authorize_repair(incident_id, responder_url=BASE_URL, confirm=True)
    if repair_resp.get("status") != "repaired":
        print(f"FAIL: Repair failed: {repair_resp}")
        return False

    print("PASS: Operator authorization accepted.")
    repair_result = repair_resp.get("repair_result", {})
    diff = repair_result.get("diff", "")
    print(f"      Diff size: {len(diff)} bytes")
    print(f"      Tests passed in disposable workspace: {repair_result.get('tests_passed')}")

    # 6. AFTER Verification: real HTTP behavior and persistence
    print("\n[Stage 6/7] AFTER repair: Verifying HTTP behavior and persistence...")
    after_check = run_http_task_verification_script(workspace_path)
    print(f"      Normal task status: {after_check.get('normal_status')} (expected 201)")
    print(f"      Urgent task status: {after_check.get('urgent_status')} (expected 201)")
    print(f"      Urgent task persisted in DB: {after_check.get('urgent_persisted')} (expected True)")
    assert after_check.get("normal_status") == 201, "Normal task must return 201"
    assert after_check.get("urgent_status") == 201, "Urgent task must return 201 after repair"
    assert after_check.get("urgent_persisted") is True, "Urgent task must be persisted in DB"
    print("PASS: Full user-journey recovery confirmed.")

    # 7. Grafana resolved notification
    print("\n[Stage 7/7] Sending Grafana resolved notification...")
    resolved_payload = {
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",
                "labels": {
                    "alertname": EXPECTED_ALERT_NAME,
                    "service": "tasklane-api",
                },
            }
        ],
    }
    resolved_resp = post_json(f"{BASE_URL}/alerts", resolved_payload)
    print(f"PASS: Resolved acknowledged: {resolved_resp.get('status')}")

    # Cleanup
    cleanup_disposable_workspace(workspace_path, incident_id)
    print("\n" + "=" * 60)
    print("ALL 7 STAGES COMPLETED SUCCESSFULLY")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = run_incident_drill()
    sys.exit(0 if success else 1)
