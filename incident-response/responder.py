"""TaskLane Issue #4: Autonomous On-Call Incident Responder Server.

Listens on port 8001 for local Grafana alerts.
Guarantees:
- Rejects malformed, oversized, resolved, or unrelated alerts.
- Deduplicates rapid notifications to prevent overlapping repairs.
- Performs strictly READ-ONLY diagnosis by default upon alert receipt.
- Requires explicit operator approval via POST /authorize-repair for write-capable remediation.
- Restricts writes exclusively to validated disposable workspaces.
"""
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_adapter import BaseAgentAdapter, CodexAgentAdapter, MockAgentAdapter
from collector import collect_incident_context
from config import (
    DIAGNOSIS_TIMEOUT_SECONDS,
    EXPECTED_ALERT_NAME,
    LOKI_URL,
    MAX_PAYLOAD_BYTES,
    PROMETHEUS_URL,
    RESPONDER_HOST,
    RESPONDER_PORT,
    TEMPO_URL,
)
from sandbox import SecurityViolation, validate_safe_workspace_path

# State store
_lock = threading.Lock()
_current_status = "idle"  # idle | diagnosing | diagnosed | repairing | repaired
_current_incident: Optional[Dict[str, Any]] = None
_incident_history: List[Dict[str, Any]] = []

# Active adapter (default to MockAgentAdapter, can be switched via set_adapter)
_active_adapter: BaseAgentAdapter = MockAgentAdapter()


def get_active_adapter() -> BaseAgentAdapter:
    global _active_adapter
    return _active_adapter


def set_active_adapter(adapter: BaseAgentAdapter) -> None:
    global _active_adapter
    _active_adapter = adapter


def reset_state() -> None:
    """Reset server state for testing."""
    global _current_status, _current_incident, _incident_history
    with _lock:
        _current_status = "idle"
        _current_incident = None
        _incident_history.clear()


class ResponderHandler(BaseHTTPRequestHandler):
    """HTTP Request handler for on-call responder endpoints."""

    def log_message(self, format, *args):
        # Suppress noisy default logging; logs are kept bounded
        return

    def send_json(self, status_code: int, data: Dict[str, Any]) -> None:
        """Send a JSON HTTP response."""
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Handle status, health, and incident queries."""
        if self.path == "/health":
            return self.send_json(200, {
                "status": "healthy",
                "service": "tasklane-oncall-responder",
                "port": RESPONDER_PORT,
            })

        if self.path == "/status":
            with _lock:
                return self.send_json(200, {
                    "current_status": _current_status,
                    "current_incident": _current_incident,
                    "adapter": get_active_adapter().adapter_name,
                })

        if self.path == "/incidents":
            with _lock:
                return self.send_json(200, {
                    "incidents": list(_incident_history),
                })

        return self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        """Handle incoming alerts and operator repair authorizations."""
        if self.path == "/alerts":
            return self._handle_alert_webhook()

        if self.path == "/authorize-repair":
            return self._handle_authorize_repair()

        return self.send_json(404, {"error": "not_found"})

    def _handle_alert_webhook(self) -> None:
        """Process incoming Grafana alert. Enforces read-only diagnosis."""
        global _current_status, _current_incident, _incident_history

        # 1. Payload size check
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "invalid_content_length"})

        if content_length < 2:
            return self.send_json(400, {"error": "empty_payload"})
        if content_length > MAX_PAYLOAD_BYTES:
            return self.send_json(413, {"error": "payload_too_large"})

        # 2. Parse JSON
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                return self.send_json(400, {"error": "malformed_json_not_object"})
        except Exception:
            return self.send_json(400, {"error": "malformed_json"})

        # 3. Validate status: only firing alerts trigger response
        status = payload.get("status")
        if status != "firing":
            return self.send_json(400, {
                "error": "unsupported_alert_status",
                "detail": f"Expected 'firing', received '{status}'",
            })

        # 4. Validate alertname: must contain EXPECTED_ALERT_NAME
        alerts = payload.get("alerts")
        if not isinstance(alerts, list) or not alerts:
            return self.send_json(400, {"error": "missing_alerts_list"})

        matching_alert = None
        for item in alerts:
            if isinstance(item, dict):
                lbls = item.get("labels", {})
                if isinstance(lbls, dict) and lbls.get("alertname") == EXPECTED_ALERT_NAME:
                    matching_alert = item
                    break

        if not matching_alert:
            return self.send_json(400, {
                "error": "unrelated_alert",
                "detail": f"No alert matching '{EXPECTED_ALERT_NAME}' found",
            })

        # 5. Deduplication and concurrency guard
        with _lock:
            if _current_status in ("diagnosing", "diagnosed", "repairing"):
                # Duplicate notification for ongoing incident
                return self.send_json(200, {
                    "status": "deduplicated",
                    "accepted": False,
                    "detail": "Incident investigation or repair already in progress",
                    "current_status": _current_status,
                })

            incident_id = str(uuid.uuid4())[:8]
            _current_status = "diagnosing"
            _current_incident = {
                "incident_id": incident_id,
                "alertname": EXPECTED_ALERT_NAME,
                "status": "diagnosing",
                "write_authorized": False,
            }

        # 6. Read-only diagnosis execution
        incident_context = collect_incident_context(PROMETHEUS_URL, LOKI_URL, TEMPO_URL, payload)
        diagnosis_result = get_active_adapter().diagnose(incident_context)

        with _lock:
            _current_status = "diagnosed"
            _current_incident.update({
                "status": "diagnosed",
                "telemetry": incident_context,
                "diagnosis": diagnosis_result,
                "write_authorized": False,  # STRICT: read-only by default
            })
            _incident_history.append(dict(_current_incident))

        return self.send_json(200, {
            "status": "diagnosed",
            "accepted": True,
            "incident_id": incident_id,
            "read_only": True,
            "write_authorized": False,
            "diagnosis": diagnosis_result,
        })

    def _handle_authorize_repair(self) -> None:
        """Authorize write-capable remediation by an explicit local operator action."""
        global _current_status, _current_incident, _incident_history

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                return self.send_json(400, {"error": "malformed_json"})
        except Exception:
            return self.send_json(400, {"error": "invalid_payload"})

        # Operator confirmation gate
        operator = payload.get("operator")
        confirm = payload.get("confirm", False)
        workspace_path_str = payload.get("workspace_path")

        if not operator or not confirm:
            return self.send_json(403, {
                "error": "authorization_denied",
                "detail": "Explicit operator confirmation is required ('operator' and 'confirm: true')",
            })

        if not workspace_path_str:
            return self.send_json(400, {"error": "missing_workspace_path"})

        # Validate disposable workspace containment
        try:
            safe_workspace = validate_safe_workspace_path(Path(workspace_path_str))
        except SecurityViolation as sec_err:
            return self.send_json(403, {
                "error": "security_boundary_violation",
                "detail": str(sec_err),
            })

        with _lock:
            _current_status = "repairing"
            if _current_incident:
                _current_incident["status"] = "repairing"
                _current_incident["operator"] = operator
                _current_incident["write_authorized"] = True

        # Execute remediation in disposable workspace
        context = _current_incident.get("telemetry", {}) if _current_incident else {}
        try:
            repair_result = get_active_adapter().repair(
                incident_context=context,
                workspace_path=safe_workspace,
                operator_approved=True,
            )
        except Exception as exc:
            repair_result = {
                "status": "repair_exception",
                "error": str(exc),
                "tests_passed": False,
            }

        with _lock:
            _current_status = "repaired" if repair_result.get("tests_passed") else "repair_failed"
            if _current_incident:
                _current_incident["status"] = _current_status
                _current_incident["repair"] = repair_result
                _incident_history.append(dict(_current_incident))

        return self.send_json(200, {
            "status": _current_status,
            "operator": operator,
            "write_authorized": True,
            "repair_result": repair_result,
        })


def run_server(host: str = RESPONDER_HOST, port: int = RESPONDER_PORT) -> ThreadingHTTPServer:
    """Instantiate and start the on-call responder HTTP server."""
    server = ThreadingHTTPServer((host, port), ResponderHandler)
    return server


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else RESPONDER_PORT
    print(f"Starting TaskLane On-Call Responder on {RESPONDER_HOST}:{port}...", flush=True)
    server = run_server(RESPONDER_HOST, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
