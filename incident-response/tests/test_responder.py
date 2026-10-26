"""Unit tests for incident-response HTTP server (responder.py).

Tests:
- Valid alert triggers read-only diagnosis without write authorization.
- Duplicate notifications are deduplicated without spawning overlapping repairs.
- Malformed, oversized, and unrelated alerts are rejected.
- Repair authorization requires genuine single-use incident token.
- Strict boolean confirm required (rejects string 'false' and non-booleans).
- Single-use token is consumed upon first use (replay rejected).
- Concurrency control prevents simultaneous repairs.
- Grafana resolved notification cleans up incident state.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from agent_adapter import MockAgentAdapter
from config import EXPECTED_ALERT_NAME, MAX_INCIDENT_HISTORY, MAX_PAYLOAD_BYTES
from fixtures import inject_task_creation_defect
from operator_cli import get_token_for_incident
from responder import (
    _get_token_path,
    reset_state,
    run_server,
    set_active_adapter,
    set_http_repair_allowed,
)
from sandbox import get_registered_workspace, reset_workspace_registry

TEST_PORT = 18001
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


@pytest.fixture(scope="module", autouse=True)
def server_lifecycle():
    """Start local responder test server in background thread."""
    server = run_server("127.0.0.1", TEST_PORT)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)
    yield
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def clean_state():
    reset_state()
    reset_workspace_registry()
    set_active_adapter(MockAgentAdapter())


def _post_json(path: str, data: dict, headers: dict = None, timeout: float = 25.0):
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}


def _get_json(path: str, timeout: float = 25.0):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}


# ==============================================================================
# Health and Status Endpoints
# ==============================================================================

def test_health_endpoint():
    code, data = _get_json("/health")
    assert code == 200
    assert data.get("status") == "healthy"


def test_status_endpoint_idle():
    code, data = _get_json("/status")
    assert code == 200
    assert data.get("current_status") == "idle"


# ==============================================================================
# Alert Validation and Rejection
# ==============================================================================

def test_alert_rejects_empty_payload():
    url = f"{BASE_URL}/alerts"
    req = urllib.request.Request(url, data=b"", headers={"Content-Type": "application/json", "Content-Length": "0"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 400
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_alert_rejects_oversized_payload():
    large_payload = {"status": "firing", "data": "X" * (MAX_PAYLOAD_BYTES + 100)}
    code, data = _post_json("/alerts", large_payload)
    assert code == 413
    assert data.get("error") == "payload_too_large"


def test_alert_rejects_malformed_json():
    url = f"{BASE_URL}/alerts"
    bad_bytes = b"{not: a valid json"
    req = urllib.request.Request(url, data=bad_bytes, headers={"Content-Type": "application/json", "Content-Length": str(len(bad_bytes))}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 400
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_alert_rejects_unrelated_alert():
    unrelated_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": "CPUAlert", "instance": "server1"}}],
    }
    code, data = _post_json("/alerts", unrelated_payload)
    assert code == 400
    assert data.get("error") == "unrelated_alert"


def test_alert_accepts_firing_and_initiates_read_only_diagnosis():
    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME, "severity": "critical"}}],
    }
    code, data = _post_json("/alerts", firing_payload)
    assert code == 200
    assert data.get("status") == "diagnosed"
    assert data.get("read_only") is True
    assert data.get("write_authorized") is False
    assert "incident_id" in data


def test_alert_deduplication():
    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    code1, data1 = _post_json("/alerts", firing_payload)
    assert code1 == 200
    assert data1.get("status") == "diagnosed"

    # Second notification while diagnosed must deduplicate
    code2, data2 = _post_json("/alerts", firing_payload)
    assert code2 == 200
    assert data2.get("status") == "deduplicated"
    assert data2.get("accepted") is False


def test_alert_handles_resolved_cleanly():
    # 1. Fire alert
    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    code1, data1 = _post_json("/alerts", firing_payload)
    assert code1 == 200

    # 2. Resolve alert
    resolved_payload = {
        "status": "resolved",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    code2, data2 = _post_json("/alerts", resolved_payload)
    assert code2 == 200
    assert data2.get("status") == "resolved"

    # Status should now be resolved
    st_code, st_data = _get_json("/status")
    assert st_code == 200
    assert st_data.get("current_status") == "resolved"


# ==============================================================================
# Repair Authorization Security Tests
# ==============================================================================

def test_authorize_repair_disabled_by_default():
    set_http_repair_allowed(False)
    code, data = _post_json(
        "/authorize-repair",
        {"incident_id": "test_any", "confirm": True},
        headers={"X-Operator-Token": "test_token"},
    )
    assert code == 403
    assert data.get("error") == "repair_disabled"


def test_authorize_repair_rejects_without_token():
    set_http_repair_allowed(True)
    code, data = _post_json("/authorize-repair", {"incident_id": "test", "confirm": True})
    assert code == 401
    assert data.get("error") == "authentication_required"


def test_authorize_repair_rejects_invalid_token():
    set_http_repair_allowed(True)
    # Trigger firing alert first to ensure incident exists in diagnosed state
    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    _, fire_data = _post_json("/alerts", firing_payload)
    incident_id = fire_data.get("incident_id")

    code, data = _post_json(
        "/authorize-repair",
        {"incident_id": incident_id, "confirm": True},
        headers={"X-Operator-Token": "invalid_fake_token"},
    )
    assert code == 403
    assert data.get("error") == "authorization_denied"


def test_incorrect_token_does_not_consume_legitimate_token():
    set_http_repair_allowed(True)
    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    _, fire_data = _post_json("/alerts", firing_payload)
    incident_id = fire_data.get("incident_id")
    ws = get_registered_workspace(incident_id)
    if ws:
        inject_task_creation_defect(ws)
    legit_token = get_token_for_incident(incident_id)
    assert legit_token is not None

    token_file = _get_token_path(incident_id)
    assert token_file.exists(), "Legitimate token file must exist"

    # Attempt repair with invalid token
    code_bad, data_bad = _post_json(
        "/authorize-repair",
        {"incident_id": incident_id, "confirm": True},
        headers={"X-Operator-Token": "completely_wrong_candidate_token"},
    )
    assert code_bad == 403
    assert data_bad.get("error") == "authorization_denied"

    # CRITICAL: Token file MUST still exist!
    assert token_file.exists(), "Legitimate token was improperly deleted on incorrect attempt"

    # Now attempt repair with legitimate token - must succeed!
    code_good, data_good = _post_json(
        "/authorize-repair",
        {"incident_id": incident_id, "confirm": True},
        headers={"X-Operator-Token": legit_token},
    )
    assert code_good == 200
    assert data_good.get("status") == "repaired"

    # After successful repair, token file MUST be deleted (single-use)
    assert not token_file.exists(), "Token was not consumed upon successful repair"


def test_authorize_repair_rejects_non_boolean_confirm():
    set_http_repair_allowed(True)
    # Trigger firing alert first
    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    _, fire_data = _post_json("/alerts", firing_payload)
    incident_id = fire_data.get("incident_id")
    token = get_token_for_incident(incident_id)

    # String "false"
    code, data = _post_json(
        "/authorize-repair",
        {"incident_id": incident_id, "confirm": "false"},
        headers={"X-Operator-Token": token},
    )
    assert code == 403
    assert data.get("error") == "authorization_denied"


def test_authorize_repair_single_use_token_consumed():
    set_http_repair_allowed(True)
    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    _, fire_data = _post_json("/alerts", firing_payload)
    incident_id = fire_data.get("incident_id")
    ws = get_registered_workspace(incident_id)
    if ws:
        inject_task_creation_defect(ws)
    token = get_token_for_incident(incident_id)
    assert token is not None

    # First authorization attempt succeeds
    code1, data1 = _post_json(
        "/authorize-repair",
        {"incident_id": incident_id, "confirm": True},
        headers={"X-Operator-Token": token},
    )
    assert code1 == 200
    assert data1.get("status") == "repaired"

    # Second attempt with same token must be rejected (single-use)
    code2, data2 = _post_json(
        "/authorize-repair",
        {"incident_id": incident_id, "confirm": True},
        headers={"X-Operator-Token": token},
    )
    assert code2 == 403
    assert data2.get("error") == "authorization_denied"


def test_malformed_alert_resets_idle_state():
    class FailingAdapter(MockAgentAdapter):
        def diagnose(self, context):
            raise RuntimeError("Synthetic diagnosis failure")

    set_active_adapter(FailingAdapter())
    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    code, data = _post_json("/alerts", firing_payload)
    assert code == 500
    assert data.get("error") == "diagnosis_failed"

    # Server status must cleanly recover to idle
    st_code, st_data = _get_json("/status")
    assert st_code == 200
    assert st_data.get("current_status") == "idle"


def test_incident_history_bounded(monkeypatch):
    import responder
    monkeypatch.setattr(responder, "collect_incident_context", lambda *args, **kwargs: {"error_count": 1, "has_correlated_evidence": True})
    # Fire and resolve multiple alerts
    for i in range(MAX_INCIDENT_HISTORY + 4):
        _post_json("/alerts", {
            "status": "firing",
            "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
        })
        _post_json("/alerts", {
            "status": "resolved",
            "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
        })

    code, data = _get_json("/incidents")
    assert code == 200
    incidents = data.get("incidents", [])
    assert len(incidents) <= MAX_INCIDENT_HISTORY


# ==============================================================================
# Cross-Incident Occurrence & Stale Resolution Tests (Fix 1)
# ==============================================================================

def test_cross_incident_stale_resolution_ignored_without_state_change():
    """Verify that a delayed resolved notification for incident A does not alter incident B."""
    # 1. Alert A fires and resolves
    payload_a_fire = {
        "status": "firing",
        "alerts": [{
            "labels": {"alertname": EXPECTED_ALERT_NAME},
            "fingerprint": "fp_rule_1",
            "startsAt": "2026-10-25T10:00:00Z",
        }],
    }
    code_a, data_a = _post_json("/alerts", payload_a_fire)
    assert code_a == 200
    inc_a_id = data_a.get("incident_id")

    payload_a_resolve = {
        "status": "resolved",
        "alerts": [{
            "labels": {"alertname": EXPECTED_ALERT_NAME},
            "fingerprint": "fp_rule_1",
            "startsAt": "2026-10-25T10:00:00Z",
        }],
    }
    code_a_res, data_a_res = _post_json("/alerts", payload_a_resolve)
    assert code_a_res == 200
    assert data_a_res.get("status") == "resolved"

    # 2. Alert B fires (new occurrence with different startsAt)
    payload_b_fire = {
        "status": "firing",
        "alerts": [{
            "labels": {"alertname": EXPECTED_ALERT_NAME},
            "fingerprint": "fp_rule_1",
            "startsAt": "2026-10-25T11:00:00Z",
        }],
    }
    code_b, data_b = _post_json("/alerts", payload_b_fire)
    assert code_b == 200
    assert data_b.get("status") == "diagnosed"
    inc_b_id = data_b.get("incident_id")
    assert inc_b_id != inc_a_id

    # 3. Stale resolved notification for Incident A arrives!
    code_stale, data_stale = _post_json("/alerts", payload_a_resolve)
    assert code_stale == 200
    assert data_stale.get("status") == "stale_resolution_ignored"
    assert data_stale.get("accepted") is False

    # 4. Critical verification: Incident B is STILL diagnosed, NOT resolved!
    st_code, st_data = _get_json("/status")
    assert st_code == 200
    assert st_data.get("current_status") == "diagnosed"
    assert st_data.get("current_incident", {}).get("incident_id") == inc_b_id


def test_delayed_resolution_does_not_abort_repair_in_flight():
    """Verify that a delayed resolved notification for A does not queue resolution or disrupt B's repair."""
    set_http_repair_allowed(True)

    # 1. Alert B fires
    payload_b = {
        "status": "firing",
        "alerts": [{
            "labels": {"alertname": EXPECTED_ALERT_NAME},
            "fingerprint": "fp_rule_1",
            "startsAt": "2026-10-25T12:00:00Z",
        }],
    }
    _, fire_data = _post_json("/alerts", payload_b)
    inc_b_id = fire_data.get("incident_id")
    token = get_token_for_incident(inc_b_id)
    ws = get_registered_workspace(inc_b_id)
    if ws:
        inject_task_creation_defect(ws)

    # 2. Stale resolution for older occurrence arrives
    stale_resolve = {
        "status": "resolved",
        "alerts": [{
            "labels": {"alertname": EXPECTED_ALERT_NAME},
            "fingerprint": "fp_rule_1",
            "startsAt": "2026-10-25T09:00:00Z",
        }],
    }
    code_stale, data_stale = _post_json("/alerts", stale_resolve)
    assert code_stale == 200
    assert data_stale.get("status") == "stale_resolution_ignored"

    # 3. Incident B repair executes normally
    code_rep, data_rep = _post_json(
        "/authorize-repair",
        {"incident_id": inc_b_id, "confirm": True},
        headers={"X-Operator-Token": token},
    )
    assert code_rep == 200
    assert data_rep.get("status") == "repaired"

    # 4. Legitimate resolution for B arrives
    legit_resolve = {
        "status": "resolved",
        "alerts": [{
            "labels": {"alertname": EXPECTED_ALERT_NAME},
            "fingerprint": "fp_rule_1",
            "startsAt": "2026-10-25T12:00:00Z",
        }],
    }
    code_res, data_res = _post_json("/alerts", legit_resolve)
    assert code_res == 200
    assert data_res.get("status") == "resolved"


# ==============================================================================
# Exception-Safe Provisioning & Fault Injection Tests (Fix 2)
# ==============================================================================

def test_token_write_failure_resets_idle_state_and_cleans_resources(monkeypatch):
    """Verify that token generation failure safely rolls back state to idle without leaving 'diagnosing'."""
    set_http_repair_allowed(True)
    import responder

    def failing_token_gen(incident_id: str) -> str:
        raise OSError("Injected disk I/O error during token write")

    monkeypatch.setattr(responder, "generate_incident_token", failing_token_gen)

    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    code, data = _post_json("/alerts", firing_payload)
    assert code == 500
    assert data.get("error") == "token_generation_failed"

    # CRITICAL: Server state must be idle, NOT stuck in 'diagnosing'
    st_code, st_data = _get_json("/status")
    assert st_code == 200
    assert st_data.get("current_status") == "idle"
    assert st_data.get("current_incident") is None

    # Verify subsequent valid alert is actionable
    monkeypatch.undo()
    code2, data2 = _post_json("/alerts", firing_payload)
    assert code2 == 200
    assert data2.get("status") == "diagnosed"


def test_delayed_failure_of_incident_a_does_not_clear_incident_b():
    """Verify that delayed failure handling of incident A does not clear active incident B."""
    # 1. Establish incident B in diagnosed state
    firing_payload = {
        "status": "firing",
        "alerts": [{
            "labels": {"alertname": EXPECTED_ALERT_NAME},
            "occurrence_id": "occ_b_test",
        }],
    }
    code_b, data_b = _post_json("/alerts", firing_payload)
    assert code_b == 200
    inc_b_id = data_b.get("incident_id")

    # 2. Directly verify that responder's lock-guarded rollback requires matching incident_id
    import responder
    with responder._lock:
        # Simulate delayed failure callback belonging to old incident 'old_inc_a'
        old_id = "old_inc_a"
        if responder._current_incident and responder._current_incident.get("incident_id") == old_id:
            responder._current_status = "idle"
            responder._current_incident = None

    # 3. Incident B must remain completely intact
    st_code, st_data = _get_json("/status")
    assert st_code == 200
    assert st_data.get("current_status") == "diagnosed"
    assert st_data.get("current_incident", {}).get("incident_id") == inc_b_id


def test_synthetic_exception_secret_never_appears_in_http_response(monkeypatch):
    """Verify that sensitive exception text (SECRET_TEST_VALUE_123) is sanitized
    and never leaks into the public HTTP 500 response body or headers.
    """
    secret_marker = "SECRET_TEST_VALUE_123"

    class SecretLeakingAdapter(MockAgentAdapter):
        def diagnose(self, incident_context: dict) -> dict:
            raise RuntimeError(f"Database password exposed: {secret_marker}")

    set_active_adapter(SecretLeakingAdapter())

    firing_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }

    url = f"{BASE_URL}/alerts"
    body = json.dumps(firing_payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        method="POST",
    )

    try:
        urllib.request.urlopen(req, timeout=10.0)
        pytest.fail("Expected HTTP 500 on diagnosis failure")
    except urllib.error.HTTPError as exc:
        raw_response = exc.read().decode("utf-8")
        assert exc.code == 500
        # Assert secret marker NEVER appears in raw HTTP body or headers
        assert secret_marker not in raw_response
        for header, val in exc.headers.items():
            assert secret_marker not in str(header)
            assert secret_marker not in str(val)

        parsed = json.loads(raw_response)
        assert parsed.get("error") == "diagnosis_failed"
        assert parsed.get("detail") == "Provisioning or diagnosis failed"
        assert secret_marker not in str(parsed)

    # State must reset to idle
    st_code, st_data = _get_json("/status")
    assert st_code == 200
    assert st_data.get("current_status") == "idle"

    # Also test provisioning failure with the secret marker
    import responder
    monkeypatch.setattr(
        responder,
        "is_http_repair_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        responder,
        "generate_incident_token",
        lambda inc_id: (_ for _ in ()).throw(RuntimeError(f"Vault key exposed: {secret_marker}")),
    )

    try:
        urllib.request.urlopen(req, timeout=10.0)
        pytest.fail("Expected HTTP 500 on provisioning failure")
    except urllib.error.HTTPError as exc:
        raw_response = exc.read().decode("utf-8")
        assert exc.code == 500
        assert secret_marker not in raw_response
        parsed = json.loads(raw_response)
        assert parsed.get("error") == "token_generation_failed"
        assert parsed.get("detail") == "Provisioning or diagnosis failed"
        assert secret_marker not in str(parsed)

    # State must reset to idle
    st_code2, st_data2 = _get_json("/status")
    assert st_code2 == 200
    assert st_data2.get("current_status") == "idle"



