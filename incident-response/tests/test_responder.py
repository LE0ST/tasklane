"""Unit tests for incident-response HTTP server (responder.py).

Tests:
- Valid alert triggers read-only diagnosis without write authorization.
- Duplicate notifications are deduplicated without spawning overlapping repairs.
- Malformed, oversized, resolved, and unrelated alerts are rejected.
- Repair authorization requires explicit operator confirmation and safe workspace paths.
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
from config import DISPOSABLE_BASE_DIR, EXPECTED_ALERT_NAME, MAX_PAYLOAD_BYTES
from fixtures import inject_task_creation_defect
from responder import reset_state, run_server, set_active_adapter
from sandbox import create_disposable_workspace, cleanup_disposable_workspace

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
    set_active_adapter(MockAgentAdapter())


def _post_json(path: str, data: dict, timeout: float = 25.0):
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        method="POST",
    )
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
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400


def test_alert_rejects_malformed_json():
    url = f"{BASE_URL}/alerts"
    req = urllib.request.Request(url, data=b"{malformed json", headers={"Content-Type": "application/json", "Content-Length": "15"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400


def test_alert_rejects_resolved_status():
    payload = {
        "status": "resolved",
        "alerts": [{"status": "resolved", "labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    code, data = _post_json("/alerts", payload)
    assert code == 400
    assert data.get("error") == "unsupported_alert_status"


def test_alert_rejects_missing_alerts_list():
    payload = {"status": "firing", "alerts": []}
    code, data = _post_json("/alerts", payload)
    assert code == 400
    assert data.get("error") == "missing_alerts_list"


def test_alert_rejects_unrelated_alert():
    payload = {
        "status": "firing",
        "alerts": [{"status": "firing", "labels": {"alertname": "HighCPUUsage"}}],
    }
    code, data = _post_json("/alerts", payload)
    assert code == 400
    assert data.get("error") == "unrelated_alert"


def test_alert_rejects_oversized_payload():
    large_str = "x" * (MAX_PAYLOAD_BYTES + 100)
    url = f"{BASE_URL}/alerts"
    req = urllib.request.Request(
        url,
        data=large_str.encode("utf-8"),
        headers={"Content-Type": "application/json", "Content-Length": str(len(large_str))},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 413


# ==============================================================================
# Valid Alert & Read-Only Diagnosis
# ==============================================================================

def test_valid_firing_alert_initiates_read_only_diagnosis():
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": EXPECTED_ALERT_NAME, "service": "tasklane-api"},
                "annotations": {"summary": "TaskLane local API task operation returned HTTP 5xx"},
            }
        ],
    }
    code, data = _post_json("/alerts", payload)
    assert code == 200
    assert data.get("status") == "diagnosed"
    assert data.get("accepted") is True
    # Strictly read-only; write authorization must NEVER be granted by webhook
    assert data.get("read_only") is True
    assert data.get("write_authorized") is False
    assert "diagnosis" in data


def test_duplicate_notification_deduplicated():
    payload = {
        "status": "firing",
        "alerts": [{"status": "firing", "labels": {"alertname": EXPECTED_ALERT_NAME}}],
    }
    # First delivery
    code1, data1 = _post_json("/alerts", payload)
    assert code1 == 200
    assert data1.get("status") == "diagnosed"

    # Second delivery (duplicate while status is diagnosed)
    code2, data2 = _post_json("/alerts", payload)
    assert code2 == 200
    assert data2.get("status") == "deduplicated"
    assert data2.get("accepted") is False


# ==============================================================================
# Operator Authorization Gate
# ==============================================================================

def test_authorize_repair_rejects_missing_operator():
    code, data = _post_json("/authorize-repair", {"confirm": True, "workspace_path": "/tmp"})
    assert code == 403
    assert data.get("error") == "authorization_denied"


def test_authorize_repair_rejects_missing_confirm():
    code, data = _post_json("/authorize-repair", {"operator": "LE0ST", "confirm": False, "workspace_path": "/tmp"})
    assert code == 403
    assert data.get("error") == "authorization_denied"


def test_authorize_repair_rejects_path_escape():
    payload = {
        "operator": "LE0ST",
        "confirm": True,
        "workspace_path": "../../../etc",
    }
    code, data = _post_json("/authorize-repair", payload)
    assert code == 403
    assert data.get("error") == "security_boundary_violation"


def test_authorize_repair_success_with_valid_operator_and_disposable_workspace(tmp_path):
    # Set up safe workspace inside DISPOSABLE_BASE_DIR
    repo_root = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo_root, base_dir=tmp_path)
    inject_task_creation_defect(ws)

    try:
        # First diagnose
        _post_json("/alerts", {
            "status": "firing",
            "alerts": [{"status": "firing", "labels": {"alertname": EXPECTED_ALERT_NAME}}],
        })

        # Authorize repair explicitly
        payload = {
            "operator": "LE0ST",
            "confirm": True,
            "workspace_path": str(ws),
        }
        code, data = _post_json("/authorize-repair", payload)
        assert code == 200
        assert data.get("write_authorized") is True
        assert data.get("operator") == "LE0ST"
        assert data.get("repair_result", {}).get("status") == "repaired"
        assert "diff" in data.get("repair_result", {})
    finally:
        cleanup_disposable_workspace(ws, base_dir=tmp_path)
