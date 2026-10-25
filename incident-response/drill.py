#!/usr/bin/env python3
"""TaskLane Issue #4: End-to-end On-Call Incident & Remediation Drill.

Executes the verified before -> diagnosis -> operator approval -> repair -> verification -> recovery lifecycle.
TRUTHFUL REPORTING: Explicitly logs the active adapter name (mock vs real AI).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IR_DIR = REPO_ROOT / "incident-response"
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from config import EXPECTED_ALERT_NAME, RESPONDER_HOST, RESPONDER_PORT
from fixtures import inject_task_creation_defect
from sandbox import (
    cleanup_disposable_workspace,
    create_disposable_workspace,
    run_disposable_tests,
)


def _http_post(url: str, payload: dict, timeout: float = 10.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Content-Length": str(len(data))},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _http_get(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def run_drill():
    print("=" * 70)
    print("TASKLANE ISSUE #4: AUTONOMOUS ON-CALL REMEDIATION DRILL")
    print("=" * 70)

    base_url = f"http://127.0.0.1:{RESPONDER_PORT}"

    # Step 1: Health check
    print("\n[Step 1/7] Checking on-call responder health...")
    try:
        st, health_data = _http_get(f"{base_url}/health")
        assert st == 200 and health_data.get("status") == "healthy"
        print(f" PASS: Responder online at {base_url} (service={health_data.get('service')})")
    except Exception as exc:
        print(f" FAIL: Responder not reachable at {base_url}: {exc}")
        print(" Please start the responder first: python incident-response/responder.py")
        sys.exit(1)

    # Step 2: Provision isolated disposable workspace with bug fixture
    print("\n[Step 2/7] Provisioning isolated disposable workspace with deterministic bug fixture...")
    ws = create_disposable_workspace(REPO_ROOT)
    inject_task_creation_defect(ws)
    print(f" PASS: Disposable workspace created at: {ws}")
    print(" PASS: Bug fixture injected strictly in disposable copy (main checkout untouched).")

    # Step 3: Before - Verify bug triggers failure in disposable workspace
    print("\n[Step 3/7] BEFORE: Verifying task creation failure in disposable workspace...")
    tests_before_passed, before_out = run_disposable_tests(ws)
    assert not tests_before_passed, f"Expected tests to fail with bug fixture present!\n{before_out}"
    print(" PASS: Verified failing baseline: tests failed as expected due to injected defect.")

    try:
        # Step 4: Alert Firing -> Read-Only Diagnosis
        print("\n[Step 4/7] Simulating Grafana TaskLane5xxRate alert firing webhook...")
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
                        "summary": "Local API task operation returned HTTP 5xx",
                        "description": "UNCAUGHT REGRESSION: Failed to dispatch urgent task in backend/main.py",
                    },
                }
            ],
        }
        st_alert, alert_resp = _http_post(f"{base_url}/alerts", alert_payload)
        assert st_alert == 200, f"Expected 200 from /alerts, got {st_alert}: {alert_resp}"
        assert alert_resp.get("status") == "diagnosed"
        assert alert_resp.get("read_only") is True
        assert alert_resp.get("write_authorized") is False
        diag = alert_resp.get("diagnosis", {})
        active_adapter = diag.get("adapter", "unknown")
        print(f" PASS: Alert received. Read-only diagnosis performed (adapter={active_adapter}).")
        print(f"       Root cause finding: {diag.get('root_cause')}")
        print(f"       Write authorization: {alert_resp.get('write_authorized')} (strictly denied by default)")

        # Step 5: Explicit Operator Authorization
        print("\n[Step 5/7] Requesting explicit operator repair authorization...")
        auth_payload = {
            "operator": "LE0ST",
            "confirm": True,
            "workspace_path": str(ws),
        }
        st_repair, repair_resp = _http_post(f"{base_url}/authorize-repair", auth_payload)
        assert st_repair == 200, f"Expected 200 from /authorize-repair, got {st_repair}: {repair_resp}"
        assert repair_resp.get("write_authorized") is True
        assert repair_resp.get("operator") == "LE0ST"
        repair_res = repair_resp.get("repair_result", {})
        print(" PASS: Operator authorization granted by LE0ST.")
        print(f" PASS: Repair executed in disposable workspace (status={repair_res.get('status')}).")

        # Step 6: Verify Code Diff
        print("\n[Step 6/7] REPAIR DIFF: Inspecting agent-produced modification...")
        diff = repair_res.get("diff", "")
        if diff:
            print("--- BEGIN SANITIZED DIFF ---")
            for line in diff.splitlines()[:15]:
                print(f"  {line}")
            print("--- END SANITIZED DIFF ---")
            print(" PASS: Verified genuine code modification applied to disposable workspace.")
        else:
            print(" NOTE: No text diff returned; checking test outcome.")

        # Step 7: AFTER - Verification & Recovery
        print("\n[Step 7/7] AFTER: Verifying test suite passes and application recovers...")
        tests_after_passed, after_out = run_disposable_tests(ws)
        assert tests_after_passed, f"Tests did not pass after repair!\n{after_out}"
        print(" PASS: All automated backend tests PASSED in the repaired disposable workspace.")
        print(" PASS: User-facing task creation behavior verified restored.")

        print("\n" + "=" * 70)
        print("ON-CALL DRILL COMPLETED SUCCESSFULLY")
        print(f"Adapter Used: {active_adapter}")
        if active_adapter == "mock_agent":
            print("EVIDENCE NOTE: Executed with deterministic MockAgentAdapter (CI/policy compliant).")
        else:
            print(f"EVIDENCE NOTE: Executed with live AI adapter ({active_adapter}).")
        print("=" * 70)

    finally:
        print("\nTearing down disposable workspace...")
        cleanup_disposable_workspace(ws)
        print("Cleanup complete.")


if __name__ == "__main__":
    run_drill()
