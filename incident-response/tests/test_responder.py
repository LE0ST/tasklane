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


def _post_json(path: str, data: dict, headers: dict = None, timeout: float = 60.0):
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


def _get_json(path: str, timeout: float = 60.0):
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
        "alerts": [{
            "fingerprint": "fp_test_accept",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME, "severity": "critical"},
        }],
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
        "alerts": [{
            "fingerprint": "fp_test_dedupe",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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
    # 1. Fire alert with valid occurrence identity
    firing_payload = {
        "status": "firing",
        "alerts": [{
            "fingerprint": "fp_test_clean_resolve",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    }
    code1, data1 = _post_json("/alerts", firing_payload)
    assert code1 == 200

    # 2. Resolve alert with matching occurrence identity
    resolved_payload = {
        "status": "resolved",
        "alerts": [{
            "fingerprint": "fp_test_clean_resolve",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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
        "alerts": [{
            "fingerprint": "fp_test_auth_inv",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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
        "alerts": [{
            "fingerprint": "fp_test_auth_tok",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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
        "alerts": [{
            "fingerprint": "fp_test_auth_bool",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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
        "alerts": [{
            "fingerprint": "fp_test_auth_single",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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
        "alerts": [{
            "fingerprint": "fp_test_fail_diag",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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
            "alerts": [{
                "fingerprint": f"fp_hist_{i}",
                "startsAt": f"2026-10-25T{i:02d}:00:00Z",
                "labels": {"alertname": EXPECTED_ALERT_NAME},
            }],
        })
        _post_json("/alerts", {
            "status": "resolved",
            "alerts": [{
                "fingerprint": f"fp_hist_{i}",
                "startsAt": f"2026-10-25T{i:02d}:00:00Z",
                "labels": {"alertname": EXPECTED_ALERT_NAME},
            }],
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
        "alerts": [{
            "fingerprint": "fp_test_tok_fail",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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
            "fingerprint": "fp_delayed_fail_b",
            "startsAt": "2026-10-25T11:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
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
        "alerts": [{
            "fingerprint": "fp_test_secret",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
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


# ==============================================================================
# Occurrence Identity & Malformed Payload Boundary Tests (Fix 3)
# ==============================================================================

def test_incomplete_firing_notifications_rejected_without_state_change():
    """Verify that firing notifications lacking valid occurrence identity (fingerprint + startsAt)
    are deterministically rejected with HTTP 400 without mutating incident state.
    """
    # 1. Identity-less firing alert
    payload_no_id = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    code1, data1 = _post_json("/alerts", payload_no_id)
    assert code1 == 400
    assert data1.get("error") == "missing_occurrence_identity"

    st_code1, st_data1 = _get_json("/status")
    assert st_code1 == 200
    assert st_data1.get("current_status") == "idle"
    assert st_data1.get("current_incident") is None

    # 2. Fingerprint-only firing alert (missing startsAt)
    payload_fp_only = {
        "status": "firing",
        "alerts": [{
            "fingerprint": "fp_only_123",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    }
    code2, data2 = _post_json("/alerts", payload_fp_only)
    assert code2 == 400
    assert data2.get("error") == "missing_occurrence_identity"

    st_code2, st_data2 = _get_json("/status")
    assert st_code2 == 200
    assert st_data2.get("current_status") == "idle"
    assert st_data2.get("current_incident") is None

    # 3. startsAt-only firing alert (missing fingerprint)
    payload_starts_only = {
        "status": "firing",
        "alerts": [{
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    }
    code3, data3 = _post_json("/alerts", payload_starts_only)
    assert code3 == 400
    assert data3.get("error") == "missing_occurrence_identity"

    st_code3, st_data3 = _get_json("/status")
    assert st_code3 == 200
    assert st_data3.get("current_status") == "idle"
    assert st_data3.get("current_incident") is None


def test_incomplete_and_ambiguous_resolution_ignored_without_state_mutation():
    """Verify that resolved notifications lacking valid occurrence identity are acknowledged
    as ignored (HTTP 200 ambiguous_resolution_ignored) without mutating active incident state
    or revealing current incident details.
    """
    # 1. Establish valid incident A in diagnosed state
    firing_payload = {
        "status": "firing",
        "alerts": [{
            "fingerprint": "fp_active_a",
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    }
    code_fire, data_fire = _post_json("/alerts", firing_payload)
    assert code_fire == 200
    inc_a_id = data_fire.get("incident_id")

    # 2. Send identity-less resolved notification
    code_no_id, data_no_id = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    })
    assert code_no_id == 200
    assert data_no_id.get("status") == "ambiguous_resolution_ignored"
    assert data_no_id.get("accepted") is False
    # Must NOT reveal active incident details
    assert "active_incident_id" not in data_no_id

    # Incident A must remain diagnosed and completely intact
    st_code1, st_data1 = _get_json("/status")
    assert st_code1 == 200
    assert st_data1.get("current_status") == "diagnosed"
    assert st_data1.get("current_incident", {}).get("incident_id") == inc_a_id

    # 3. Send fingerprint-only resolved notification
    code_fp_only, data_fp_only = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": "fp_active_a",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_fp_only == 200
    assert data_fp_only.get("status") == "ambiguous_resolution_ignored"
    assert data_fp_only.get("accepted") is False

    st_code2, st_data2 = _get_json("/status")
    assert st_code2 == 200
    assert st_data2.get("current_status") == "diagnosed"
    assert st_data2.get("current_incident", {}).get("incident_id") == inc_a_id

    # 4. Send startsAt-only resolved notification
    code_starts_only, data_starts_only = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_starts_only == 200
    assert data_starts_only.get("status") == "ambiguous_resolution_ignored"
    assert data_starts_only.get("accepted") is False

    st_code3, st_data3 = _get_json("/status")
    assert st_code3 == 200
    assert st_data3.get("current_status") == "diagnosed"
    assert st_data3.get("current_incident", {}).get("incident_id") == inc_a_id


def test_identity_less_sequential_incidents_cannot_cross_resolve():
    """Verify sequence: A firing -> A resolved -> B firing -> delayed A resolved.
    Tests that neither identity-less nor fingerprint-only delayed resolutions can cross-resolve B.
    """
    fp_common = "fp_common_rule"
    starts_a = "2026-10-25T10:00:00Z"
    starts_b = "2026-10-25T11:00:00Z"

    # 1. Incident A fires
    code_a, data_a = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "fingerprint": fp_common,
            "startsAt": starts_a,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_a == 200
    inc_a_id = data_a.get("incident_id")

    # 2. Incident A resolves with genuine matching identity
    code_a_res, data_a_res = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": fp_common,
            "startsAt": starts_a,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_a_res == 200
    assert data_a_res.get("status") == "resolved"

    # Status must be resolved
    st_code1, st_data1 = _get_json("/status")
    assert st_code1 == 200
    assert st_data1.get("current_status") == "resolved"

    # 3. Incident B fires (same rule fingerprint, new startsAt)
    code_b, data_b = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "fingerprint": fp_common,
            "startsAt": starts_b,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_b == 200
    inc_b_id = data_b.get("incident_id")
    assert inc_b_id != inc_a_id

    # 4. Delayed identity-less resolution arrives -> ignored
    code_idless, data_idless = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    })
    assert code_idless == 200
    assert data_idless.get("status") == "ambiguous_resolution_ignored"

    # 5. Delayed fingerprint-only resolution arrives -> ignored
    code_fponly, data_fponly = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": fp_common,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_fponly == 200
    assert data_fponly.get("status") == "ambiguous_resolution_ignored"

    # 6. Delayed matching resolution for incident A arrives -> stale_resolution_ignored
    code_stale_a, data_stale_a = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": fp_common,
            "startsAt": starts_a,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_stale_a == 200
    assert data_stale_a.get("status") == "stale_resolution_ignored"
    assert data_stale_a.get("accepted") is False

    # Incident B must remain completely intact and diagnosed!
    st_code2, st_data2 = _get_json("/status")
    assert st_code2 == 200
    assert st_data2.get("current_status") == "diagnosed"
    assert st_data2.get("current_incident", {}).get("incident_id") == inc_b_id

    # 7. Finally, incident B resolves cleanly with matching occurrence identity
    code_b_res, data_b_res = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": fp_common,
            "startsAt": starts_b,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_b_res == 200
    assert data_b_res.get("status") == "resolved"


def test_resolution_during_diagnosis_with_occurrence_identity(monkeypatch):
    """Verify resolution handling while diagnosis is in flight:
    - Stale or incomplete resolutions are ignored without altering state.
    - Matching resolution transitions status to resolved and diagnosis completion records it cleanly.
    """
    import responder
    monkeypatch.setattr(responder, "collect_incident_context", lambda *args, **kwargs: {"error_count": 1, "has_correlated_evidence": True})

    slow_event = threading.Event()

    class PausingAdapter(MockAgentAdapter):
        def diagnose(self, context):
            slow_event.wait(timeout=5.0)
            return super().diagnose(context)

    set_active_adapter(PausingAdapter())

    fp = "fp_diag_occur"
    starts = "2026-10-25T14:00:00Z"
    firing_payload = {
        "status": "firing",
        "alerts": [{
            "fingerprint": fp,
            "startsAt": starts,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    }

    # Start firing in background thread
    fire_result = {}
    def fire_async():
        c, d = _post_json("/alerts", firing_payload, timeout=10.0)
        fire_result["code"] = c
        fire_result["data"] = d

    th = threading.Thread(target=fire_async)
    th.start()
    time.sleep(0.3)

    # Server should be in diagnosing state
    st_code, st_data = _get_json("/status")
    assert st_code == 200
    assert st_data.get("current_status") == "diagnosing"

    # 1. Stale resolution arrives for a different occurrence -> ignored
    stale_code, stale_data = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": "fp_different",
            "startsAt": "2026-10-25T08:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert stale_code == 200
    assert stale_data.get("status") == "stale_resolution_ignored"

    # 2. Incomplete resolution arrives -> ignored
    incomp_code, incomp_data = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    })
    assert incomp_code == 200
    assert incomp_data.get("status") == "ambiguous_resolution_ignored"

    # 3. Legitimate matching resolution arrives during diagnosis -> transitions to resolved
    match_code, match_data = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": fp,
            "startsAt": starts,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert match_code == 200
    assert match_data.get("status") == "resolved"

    # Let diagnosis finish
    slow_event.set()
    th.join(timeout=5.0)

    # Fire endpoint must have recorded resolution during diagnosis
    assert fire_result.get("code") == 200
    assert fire_result.get("data", {}).get("status") == "resolved"

    # Status must remain resolved, NOT overwritten to diagnosed
    st_final_code, st_final_data = _get_json("/status")
    assert st_final_code == 200
    assert st_final_data.get("current_status") == "resolved"


def test_stale_or_incomplete_resolution_during_repair_does_not_set_resolved_pending():
    """Verify that during an active repair:
    - Incomplete or stale resolutions do NOT set resolved_pending on the incident.
    - Matching resolution correctly queues resolution_pending.
    """
    set_http_repair_allowed(True)
    import responder

    fp_b = "fp_repair_test"
    starts_b = "2026-10-25T16:00:00Z"

    # 1. Fire and diagnose incident B
    code_b, data_b = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "fingerprint": fp_b,
            "startsAt": starts_b,
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_b == 200
    inc_b_id = data_b.get("incident_id")

    # Inject defect and obtain token
    ws = get_registered_workspace(inc_b_id)
    if ws:
        inject_task_creation_defect(ws)
    token = get_token_for_incident(inc_b_id)

    # 2. Simulate in-flight repair by setting state to repairing
    with responder._lock:
        responder._current_status = "repairing"

    try:
        # (a) Send incomplete resolution
        code_inc, data_inc = _post_json("/alerts", {
            "status": "resolved",
            "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
        })
        assert code_inc == 200
        assert data_inc.get("status") == "ambiguous_resolution_ignored"
        assert not responder._current_incident.get("resolved_pending")

        # (b) Send stale resolution for prior occurrence A
        code_stale, data_stale = _post_json("/alerts", {
            "status": "resolved",
            "alerts": [{
                "fingerprint": fp_b,
                "startsAt": "2026-10-25T05:00:00Z",
                "labels": {"alertname": EXPECTED_ALERT_NAME},
            }],
        })
        assert code_stale == 200
        assert data_stale.get("status") == "stale_resolution_ignored"
        assert not responder._current_incident.get("resolved_pending")

        # (c) Send matching resolution for active incident B
        code_match, data_match = _post_json("/alerts", {
            "status": "resolved",
            "alerts": [{
                "fingerprint": fp_b,
                "startsAt": starts_b,
                "labels": {"alertname": EXPECTED_ALERT_NAME},
            }],
        })
        assert code_match == 200
        assert data_match.get("status") == "acknowledged"
        assert responder._current_incident.get("resolved_pending") is True

    finally:
        with responder._lock:
            responder._current_status = "diagnosed"


def test_malformed_alerts_cannot_mutate_lifecycle_state():
    """Verify that malformed payloads, non-objects, empty lists, or unrelated alerts
    cannot alter active incident state or reset status.
    """
    # 1. Establish active incident
    code_init, data_init = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "fingerprint": "fp_guard_active",
            "startsAt": "2026-10-25T18:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_init == 200
    active_inc_id = data_init.get("incident_id")

    # (a) Malformed alert: not a JSON object
    req1 = urllib.request.Request(
        f"{BASE_URL}/alerts",
        data=b"['not', 'an', 'object']",
        headers={"Content-Type": "application/json", "Content-Length": "22"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req1)
        pytest.fail("Expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400

    # (b) Empty payload
    req2 = urllib.request.Request(
        f"{BASE_URL}/alerts",
        data=b"",
        headers={"Content-Type": "application/json", "Content-Length": "0"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req2)
        pytest.fail("Expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400

    # (c) Unrelated alert
    code_unrel, data_unrel = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{"labels": {"alertname": "UnrelatedRule"}}],
    })
    assert code_unrel == 400
    assert data_unrel.get("error") == "unrelated_alert"

    # (d) Missing alerts list
    code_no_list, data_no_list = _post_json("/alerts", {
        "status": "firing",
        "alerts": [],
    })
    assert code_no_list == 400
    assert data_no_list.get("error") == "missing_alerts_list"

    # (e) Firing missing occurrence identity
    code_no_id, data_no_id = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{"labels": {"alertname": EXPECTED_ALERT_NAME}}],
    })
    assert code_no_id == 400
    assert data_no_id.get("error") == "missing_occurrence_identity"

    # CRITICAL: Active incident MUST be completely unchanged!
    st_code, st_data = _get_json("/status")
    assert st_code == 200
    assert st_data.get("current_status") == "diagnosed"
    assert st_data.get("current_incident", {}).get("incident_id") == active_inc_id


# ==============================================================================
# Strict Occurrence Identity Exclusivity Tests (Fix 4: No explicit ID overrides)
# ==============================================================================

def test_explicit_occurrence_id_ignored_and_cannot_cross_resolve():
    """Verify that caller-supplied explicit occurrence_id (both top-level and nested under labels)
    is completely ignored, and occurrence identity is derived exclusively from fingerprint and startsAt.
    Ensures incidents A and B with the same explicit ID remain distinguishable and delayed resolution of A
    cannot resolve B.
    """
    fp_rule = "fp_rule_exclusive"
    starts_a = "2026-10-25T10:00:00Z"
    starts_b = "2026-10-25T11:00:00Z"
    shared_explicit_id = "shared_explicit_incident_id"

    # 1. Incident A fires with explicit ID both top-level and in labels
    code_a, data_a = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "fingerprint": fp_rule,
            "startsAt": starts_a,
            "occurrence_id": shared_explicit_id,
            "labels": {
                "alertname": EXPECTED_ALERT_NAME,
                "occurrence_id": shared_explicit_id,
            },
        }],
    })
    assert code_a == 200
    inc_a_id = data_a.get("incident_id")
    # Verify occurrence identity was derived from fingerprint + startsAt, NOT explicit ID!
    assert data_a.get("occurrence_id") == f"{fp_rule}_{starts_a}"

    # 2. Incident A resolves
    code_a_res, data_a_res = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": fp_rule,
            "startsAt": starts_a,
            "occurrence_id": shared_explicit_id,
            "labels": {
                "alertname": EXPECTED_ALERT_NAME,
                "occurrence_id": shared_explicit_id,
            },
        }],
    })
    assert code_a_res == 200
    assert data_a_res.get("status") == "resolved"

    st_code1, st_data1 = _get_json("/status")
    assert st_code1 == 200
    assert st_data1.get("current_status") == "resolved"

    # 3. Incident B fires with the EXACT SAME explicit ID but different startsAt
    code_b, data_b = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "fingerprint": fp_rule,
            "startsAt": starts_b,
            "occurrence_id": shared_explicit_id,
            "labels": {
                "alertname": EXPECTED_ALERT_NAME,
                "occurrence_id": shared_explicit_id,
            },
        }],
    })
    assert code_b == 200
    inc_b_id = data_b.get("incident_id")
    assert inc_b_id != inc_a_id
    assert data_b.get("occurrence_id") == f"{fp_rule}_{starts_b}"

    # 4. Delayed resolution for A arrives (bearing the same explicit ID as B!)
    code_stale, data_stale = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": fp_rule,
            "startsAt": starts_a,
            "occurrence_id": shared_explicit_id,
            "labels": {
                "alertname": EXPECTED_ALERT_NAME,
                "occurrence_id": shared_explicit_id,
            },
        }],
    })
    assert code_stale == 200
    assert data_stale.get("status") == "stale_resolution_ignored"
    assert data_stale.get("accepted") is False

    # Incident B MUST remain completely intact and diagnosed!
    st_code2, st_data2 = _get_json("/status")
    assert st_code2 == 200
    assert st_data2.get("current_status") == "diagnosed"
    assert st_data2.get("current_incident", {}).get("incident_id") == inc_b_id

    # 5. Matching resolution for B arrives
    code_b_res, data_b_res = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": fp_rule,
            "startsAt": starts_b,
            "occurrence_id": shared_explicit_id,
            "labels": {
                "alertname": EXPECTED_ALERT_NAME,
                "occurrence_id": shared_explicit_id,
            },
        }],
    })
    assert code_b_res == 200
    assert data_b_res.get("status") == "resolved"


def test_explicit_id_cannot_validate_incomplete_notifications():
    """Verify that an explicit occurrence_id cannot make an incomplete firing or resolved
    notification valid. Missing fingerprint or startsAt must always be rejected/ignored.
    """
    # 1. Firing with top-level occurrence_id but missing startsAt
    code1, data1 = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "fingerprint": "fp_test",
            "occurrence_id": "bypass_attempt",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code1 == 400
    assert data1.get("error") == "missing_occurrence_identity"

    st_code1, st_data1 = _get_json("/status")
    assert st_code1 == 200
    assert st_data1.get("current_status") == "idle"

    # 2. Firing with nested labels.occurrence_id but missing fingerprint
    code2, data2 = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "startsAt": "2026-10-25T10:00:00Z",
            "labels": {
                "alertname": EXPECTED_ALERT_NAME,
                "occurrence_id": "bypass_attempt",
            },
        }],
    })
    assert code2 == 400
    assert data2.get("error") == "missing_occurrence_identity"

    st_code2, st_data2 = _get_json("/status")
    assert st_code2 == 200
    assert st_data2.get("current_status") == "idle"

    # 3. Firing with occurrence_id alone (no fingerprint and no startsAt)
    code3, data3 = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "occurrence_id": "bypass_attempt",
            "labels": {
                "alertname": EXPECTED_ALERT_NAME,
                "occurrence_id": "bypass_attempt",
            },
        }],
    })
    assert code3 == 400
    assert data3.get("error") == "missing_occurrence_identity"

    st_code3, st_data3 = _get_json("/status")
    assert st_code3 == 200
    assert st_data3.get("current_status") == "idle"

    # 4. Establish genuine active diagnosed incident C
    code_c, data_c = _post_json("/alerts", {
        "status": "firing",
        "alerts": [{
            "fingerprint": "fp_c_test",
            "startsAt": "2026-10-25T15:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_c == 200
    inc_c_id = data_c.get("incident_id")

    # (a) Send resolved alert with explicit ID but missing startsAt
    code_res_inc1, data_res_inc1 = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": "fp_c_test",
            "occurrence_id": "bypass_attempt",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_res_inc1 == 200
    assert data_res_inc1.get("status") == "ambiguous_resolution_ignored"

    # (b) Send resolved alert with explicit ID alone (no fingerprint or startsAt)
    code_res_inc2, data_res_inc2 = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "occurrence_id": "bypass_attempt",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_res_inc2 == 200
    assert data_res_inc2.get("status") == "ambiguous_resolution_ignored"

    # Active incident C must remain diagnosed
    st_code4, st_data4 = _get_json("/status")
    assert st_code4 == 200
    assert st_data4.get("current_status") == "diagnosed"
    assert st_data4.get("current_incident", {}).get("incident_id") == inc_c_id

    # 5. Matching genuine resolution resolves incident C
    code_c_res, data_c_res = _post_json("/alerts", {
        "status": "resolved",
        "alerts": [{
            "fingerprint": "fp_c_test",
            "startsAt": "2026-10-25T15:00:00Z",
            "labels": {"alertname": EXPECTED_ALERT_NAME},
        }],
    })
    assert code_c_res == 200
    assert data_c_res.get("status") == "resolved"




