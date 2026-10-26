"""TaskLane Issue #4: Autonomous On-Call Incident Responder Server.

Listens on port 8001 for local Grafana alerts.
Guarantees:
- Rejects malformed, oversized, or unrelated alerts.
- Deduplicates rapid notifications to prevent overlapping repairs.
- Performs strictly READ-ONLY diagnosis by default upon alert receipt.
- Manages service-owned disposable workspaces bound to incident IDs.
- Requires genuine single-use, incident-bound token authentication for repair authorization.
- Strict boolean confirm check (rejects string 'false' or truthy non-booleans).
- Atomic concurrency controls: at most one active repair at a time.
- Handles Grafana resolved notifications cleanly without locking future incidents.
- Returns fixed error codes and sanitized responses without raw exceptions or credentials.
"""
import json
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_adapter import BaseAgentAdapter, CodexAgentAdapter, MockAgentAdapter
from collector import collect_incident_context
from config import (
    DIAGNOSIS_TIMEOUT_SECONDS,
    DISPOSABLE_SERVICE_DIR,
    EXPECTED_ALERT_NAME,
    LOKI_URL,
    MAX_PAYLOAD_BYTES,
    PROMETHEUS_URL,
    RESPONDER_HOST,
    RESPONDER_PORT,
    TEMPO_URL,
)
from sandbox import (
    SecurityViolation,
    cleanup_disposable_workspace,
    create_disposable_workspace,
    get_registered_workspace,
    reset_workspace_registry,
    validate_safe_workspace_path,
)

# State store
_lock = threading.Lock()
_current_status = "idle"  # idle | diagnosing | diagnosed | repairing | repaired | resolved
_current_incident: Optional[Dict[str, Any]] = None
_incident_history: List[Dict[str, Any]] = []

# Active adapter (default to MockAgentAdapter for deterministic CI safety)
_active_adapter: BaseAgentAdapter = MockAgentAdapter()


def get_active_adapter() -> BaseAgentAdapter:
    global _active_adapter
    return _active_adapter


def set_active_adapter(adapter: BaseAgentAdapter) -> None:
    global _active_adapter
    _active_adapter = adapter


def reset_state() -> None:
    """Reset server state and cleanup for testing."""
    global _current_status, _current_incident, _incident_history
    with _lock:
        _current_status = "idle"
        _current_incident = None
        _incident_history.clear()
        reset_workspace_registry()


def _get_token_path(incident_id: str) -> Path:
    """Get path to the private single-use token file for an incident."""
    return DISPOSABLE_SERVICE_DIR / f"auth_{incident_id}.token"


def generate_incident_token(incident_id: str) -> str:
    """Generate a single-use cryptographically secure authorization token for an incident."""
    token = secrets.token_hex(32)
    token_path = _get_token_path(incident_id)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    if os.name != "nt":
        try:
            token_path.chmod(0o600)
        except OSError:
            pass
    return token


def verify_and_consume_token(incident_id: str, candidate_token: str) -> bool:
    """Verify candidate token against stored token and immediately delete it (single-use)."""
    token_path = _get_token_path(incident_id)
    if not token_path.exists() or not candidate_token:
        return False
    try:
        expected = token_path.read_text(encoding="utf-8").strip()
        is_valid = secrets.compare_digest(expected, candidate_token.strip())
        return is_valid
    finally:
        # Single-use: delete immediately regardless of match
        try:
            token_path.unlink()
        except OSError:
            pass


class ResponderHandler(BaseHTTPRequestHandler):
    """HTTP Request handler for on-call responder endpoints."""

    def log_message(self, format, *args):
        # Suppress verbose default logging; logs are kept bounded
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
                incident_summary = None
                if _current_incident:
                    incident_summary = {
                        "incident_id": _current_incident.get("incident_id"),
                        "alertname": _current_incident.get("alertname"),
                        "status": _current_incident.get("status"),
                        "write_authorized": _current_incident.get("write_authorized"),
                        "has_correlated_evidence": _current_incident.get("has_correlated_evidence"),
                    }
                return self.send_json(200, {
                    "current_status": _current_status,
                    "current_incident": incident_summary,
                    "adapter": get_active_adapter().adapter_name,
                })

        if self.path == "/incidents":
            with _lock:
                sanitized_history = []
                for inc in _incident_history:
                    sanitized_history.append({
                        "incident_id": inc.get("incident_id"),
                        "alertname": inc.get("alertname"),
                        "status": inc.get("status"),
                        "write_authorized": inc.get("write_authorized"),
                        "has_correlated_evidence": inc.get("has_correlated_evidence"),
                        "created_at": inc.get("created_at"),
                    })
                return self.send_json(200, {
                    "incidents": sanitized_history,
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
        """Process incoming Grafana alert. Enforces read-only diagnosis by default."""
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

        status = payload.get("status")

        # 3. Validate alertname: must contain EXPECTED_ALERT_NAME
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

        # 4. Handle resolved status
        if status == "resolved":
            with _lock:
                if _current_incident:
                    inc_id = _current_incident.get("incident_id")
                    _current_incident["status"] = "resolved"
                    _current_status = "resolved"
                    # Clean up token if present
                    _token_file = _get_token_path(inc_id) if inc_id else None
                    if _token_file and _token_file.exists():
                        try:
                            _token_file.unlink()
                        except OSError:
                            pass
                    return self.send_json(200, {
                        "status": "resolved",
                        "incident_id": inc_id,
                        "detail": "Incident marked resolved by Grafana notification",
                    })
                else:
                    return self.send_json(200, {
                        "status": "acknowledged",
                        "detail": "No active incident to resolve",
                    })

        # 5. Only firing alerts trigger diagnosis
        if status != "firing":
            return self.send_json(400, {
                "error": "unsupported_alert_status",
                "detail": f"Expected 'firing' or 'resolved', received '{status}'",
            })

        # 6. Deduplication and concurrency guard
        with _lock:
            if _current_status in ("diagnosing", "diagnosed", "repairing"):
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
                "created_at": time.time(),
            }

        # 7. Provision private service disposable workspace
        repo_root = Path(__file__).resolve().parents[1]
        try:
            workspace_path = create_disposable_workspace(repo_root, incident_id)
        except Exception:
            with _lock:
                _current_status = "idle"
                _current_incident = None
            return self.send_json(500, {"error": "workspace_creation_failed"})

        # 8. Generate single-use authorization token for local operator CLI
        auth_token = generate_incident_token(incident_id)

        # 9. Read-only diagnosis execution
        incident_context = collect_incident_context(PROMETHEUS_URL, LOKI_URL, TEMPO_URL, payload)
        diagnosis_result = get_active_adapter().diagnose(incident_context)

        with _lock:
            _current_status = "diagnosed"
            _current_incident.update({
                "status": "diagnosed",
                "telemetry": incident_context,
                "diagnosis": diagnosis_result,
                "has_correlated_evidence": incident_context.get("has_correlated_evidence", False),
                "workspace_path": str(workspace_path),
                "write_authorized": False,  # STRICT: read-only by default
            })
            _incident_history.append(dict(_current_incident))

        return self.send_json(200, {
            "status": "diagnosed",
            "accepted": True,
            "incident_id": incident_id,
            "read_only": True,
            "write_authorized": False,
            "has_correlated_evidence": incident_context.get("has_correlated_evidence", False),
            "diagnosis": diagnosis_result,
        })

    def _handle_authorize_repair(self) -> None:
        """Authorize write-capable remediation with strict authentication and incident binding.
        
        Enforces:
        - X-Operator-Token header matching single-use incident token.
        - Incident must exist and have status 'diagnosed'.
        - Strict boolean confirm (rejects strings 'false' or non-booleans).
        - Workspace is strictly service-managed (rejects arbitrary client paths).
        - Atomic concurrency guard (at most one active repair).
        """
        global _current_status, _current_incident, _incident_history

        # 1. Header token authentication
        auth_token = self.headers.get("X-Operator-Token", "").strip()
        if not auth_token:
            return self.send_json(401, {
                "error": "authentication_required",
                "detail": "Missing X-Operator-Token header",
            })

        # 2. Parse payload
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                return self.send_json(400, {"error": "malformed_json"})
        except Exception:
            return self.send_json(400, {"error": "invalid_payload"})

        incident_id = payload.get("incident_id")
        if not incident_id:
            return self.send_json(400, {"error": "missing_incident_id"})

        # 3. Strict boolean confirmation
        confirm = payload.get("confirm")
        if confirm is not True:  # STRICT: boolean identity check
            return self.send_json(403, {
                "error": "authorization_denied",
                "detail": "Strict boolean confirmation required ('confirm': true)",
            })

        # 4. Verify and immediately consume single-use token
        if not verify_and_consume_token(incident_id, auth_token):
            return self.send_json(403, {
                "error": "authorization_denied",
                "detail": "Invalid or expired operator authorization token",
            })

        # 5. Incident state validation and atomic concurrency lock
        with _lock:
            if not _current_incident or _current_incident.get("incident_id") != incident_id:
                return self.send_json(404, {"error": "incident_not_found"})

            if _current_status != "diagnosed":
                return self.send_json(409, {
                    "error": "invalid_state",
                    "detail": f"Incident state is '{_current_status}'; repair requires 'diagnosed'",
                })

            # Atomic transition to repairing
            _current_status = "repairing"
            _current_incident["status"] = "repairing"
            _current_incident["write_authorized"] = True

        # 6. Resolve service-registered workspace
        registered_ws = get_registered_workspace(incident_id)
        if not registered_ws or not registered_ws.exists():
            with _lock:
                _current_status = "repair_failed"
                _current_incident["status"] = "repair_failed"
            return self.send_json(500, {"error": "workspace_unavailable"})

        # 7. Execute remediation in the registered disposable workspace
        context = _current_incident.get("telemetry", {})
        try:
            repair_result = get_active_adapter().repair(
                incident_context=context,
                workspace_path=registered_ws,
                operator_approved=True,
            )
        except Exception:
            repair_result = {
                "status": "repair_exception",
                "tests_passed": False,
                "error": "Remediation execution encountered an internal error",
            }

        # 8. Update final state
        with _lock:
            tests_passed = repair_result.get("tests_passed", False)
            http_verified = repair_result.get("http_verified", False)
            no_defect = repair_result.get("status") == "no_defect_detected"
            success = bool(tests_passed and (http_verified or no_defect))

            _current_status = "repaired" if success else "repair_failed"
            if _current_incident:
                _current_incident["status"] = _current_status
                _current_incident["repair"] = repair_result
                _incident_history.append(dict(_current_incident))

        return self.send_json(200, {
            "status": _current_status,
            "incident_id": incident_id,
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
