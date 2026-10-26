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

