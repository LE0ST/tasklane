"""TaskLane Issue #4: Autonomous On-Call Incident Responder Server.

Listens on port 8001 for local Grafana alerts.
Guarantees:
- Rejects malformed, oversized, or unrelated alerts.
- Deduplicates rapid notifications to prevent overlapping repairs.
- Strictly READ-ONLY diagnosis by default upon alert receipt.
- Network-facing repair authorization is disabled by default in normal service mode.
  (Can be enabled for local isolated demonstration testing via TASKLANE_ALLOW_HTTP_REPAIR=true).
- Incorrect token attempts do NOT consume legitimate operator tokens.
- Incident state and token verification are executed atomically under lock.
- Resolved notifications cannot re-enable repairs while an earlier repair is active.
- Resolved events arriving during diagnosis are not overwritten by delayed diagnosis.
- Malformed alert handling safely recovers state without locking in 'diagnosing'.
- Bounded retention of incidents, workspaces, and tokens.
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
    ALLOW_HTTP_REPAIR,
    DIAGNOSIS_TIMEOUT_SECONDS,
    DISPOSABLE_SERVICE_DIR,
    EXPECTED_ALERT_NAME,
    LOKI_URL,
    MAX_INCIDENT_HISTORY,
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
    prune_stale_resources,
    reset_workspace_registry,
    validate_safe_workspace_path,
)

class ProvisioningError(Exception):
    """Raised when workspace creation or authorization token generation fails."""

    def __init__(self, message: str, code: str = "provisioning_failed"):
        super().__init__(message)
        self.code = code


class DiagnosticError(Exception):
    """Raised when telemetry collection or adapter diagnosis fails."""

    def __init__(self, message: str, code: str = "diagnosis_failed"):
        super().__init__(message)
        self.code = code


def extract_alert_occurrence_id(alert: Dict[str, Any]) -> str:
    """Extract stable occurrence identity from Grafana alert item.
    
    Grafana alert webhooks provide:
    - fingerprint: unique hash representing the alert rule and label set.
    - startsAt: ISO timestamp when this specific firing occurrence began.
    """
    fingerprint = str(alert.get("fingerprint") or "").strip()
    starts_at = str(alert.get("startsAt") or "").strip()
    labels = alert.get("labels", {}) if isinstance(alert.get("labels"), dict) else {}
    alertname = str(labels.get("alertname") or EXPECTED_ALERT_NAME).strip()

    # Check for explicit occurrence_id tag
    explicit_id = alert.get("occurrence_id") or labels.get("occurrence_id")
    if explicit_id:
        return str(explicit_id).strip()

    if fingerprint and starts_at:
        return f"{fingerprint}_{starts_at}"
    if starts_at:
        return f"{alertname}_{starts_at}"
    if fingerprint:
        return f"{fingerprint}"
    return f"{alertname}"


# State store
_lock = threading.Lock()
_current_status = "idle"  # idle | diagnosing | diagnosed | repairing | repaired | resolved
_current_incident: Optional[Dict[str, Any]] = None
_incident_history: List[Dict[str, Any]] = []
_http_repair_allowed: bool = ALLOW_HTTP_REPAIR

# Active adapter (default to MockAgentAdapter for deterministic CI safety)
_active_adapter: BaseAgentAdapter = MockAgentAdapter()


def is_http_repair_allowed() -> bool:
    global _http_repair_allowed
    return _http_repair_allowed or (os.environ.get("TASKLANE_ALLOW_HTTP_REPAIR", "false").lower() == "true")


def set_http_repair_allowed(allowed: bool) -> None:
    global _http_repair_allowed
    _http_repair_allowed = allowed


def get_active_adapter() -> BaseAgentAdapter:
    global _active_adapter
    return _active_adapter


def set_active_adapter(adapter: BaseAgentAdapter) -> None:
    global _active_adapter
    _active_adapter = adapter


def reset_state() -> None:
    """Reset server state and cleanup for testing."""
    global _current_status, _current_incident, _incident_history, _http_repair_allowed
    with _lock:
        _current_status = "idle"
        _current_incident = None
        _incident_history.clear()
        _http_repair_allowed = ALLOW_HTTP_REPAIR
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


def verify_token_match(incident_id: str, candidate_token: str) -> bool:
    """Verify candidate token against stored token without consuming it."""
    token_path = _get_token_path(incident_id)
    if not token_path.exists() or not candidate_token:
        return False
    try:
        expected = token_path.read_text(encoding="utf-8").strip()
        return secrets.compare_digest(expected, candidate_token.strip())
    except OSError:
        return False


def consume_token(incident_id: str) -> None:
    """Consume (delete) the single-use token file upon authentic match and authorization."""
    token_path = _get_token_path(incident_id)
    try:
        token_path.unlink()
    except OSError:
        pass


def check_and_consume_token(incident_id: str, candidate_token: str) -> bool:
    """Verify candidate token against stored token and consume if matching."""
    if verify_token_match(incident_id, candidate_token):
        consume_token(incident_id)
        return True
    return False


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
                incident_summary = None
                if _current_incident:
                    incident_summary = {
                        "incident_id": _current_incident.get("incident_id"),
                        "occurrence_id": _current_incident.get("occurrence_id"),
                        "alertname": _current_incident.get("alertname"),
                        "status": _current_incident.get("status"),
                        "write_authorized": _current_incident.get("write_authorized"),
                        "has_correlated_evidence": _current_incident.get("has_correlated_evidence"),
                    }
                return self.send_json(200, {
                    "current_status": _current_status,
                    "current_incident": incident_summary,
                    "adapter": get_active_adapter().adapter_name,
                    "http_repair_enabled": is_http_repair_allowed(),
                })

        if self.path == "/incidents":
            with _lock:
                sanitized_history = []
                for inc in _incident_history:
                    sanitized_history.append({
                        "incident_id": inc.get("incident_id"),
                        "occurrence_id": inc.get("occurrence_id"),
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

        occurrence_id = extract_alert_occurrence_id(matching_alert)

        # 4. Handle resolved status
        if status == "resolved":
            with _lock:
                if not _current_incident:
                    return self.send_json(200, {
                        "status": "acknowledged",
                        "detail": "No active incident to resolve",
                    })

                current_occ_id = _current_incident.get("occurrence_id")
                # Cross-incident guard:
                # If both current incident and incoming resolved notification have distinct occurrence IDs,
                # reject stale resolution without altering active incident state!
                if current_occ_id and occurrence_id and current_occ_id != occurrence_id:
                    return self.send_json(200, {
                        "status": "stale_resolution_ignored",
                        "accepted": False,
                        "detail": f"Stale resolution for occurrence '{occurrence_id}' ignored (active is '{current_occ_id}')",
                        "active_incident_id": _current_incident.get("incident_id"),
                        "active_status": _current_status,
                    })

                if _current_status == "repairing":
                    # An active repair is underway for this occurrence! Do NOT abort repair or re-enable concurrent repair
                    _current_incident["resolved_pending"] = True
                    return self.send_json(200, {
                        "status": "acknowledged",
                        "detail": "Repair currently in progress; resolution queued",
                    })

                inc_id = _current_incident.get("incident_id")
                _current_incident["status"] = "resolved"
                _current_status = "resolved"

                # Clean up resources for this incident
                ws_path = _current_incident.get("workspace_path")
                if ws_path:
                    try:
                        cleanup_disposable_workspace(Path(ws_path), inc_id)
                    except Exception:
                        pass
                _token_file = _get_token_path(inc_id) if inc_id else None
                if _token_file and _token_file.exists():
                    try:
                        _token_file.unlink()
                    except OSError:
                        pass

                return self.send_json(200, {
                    "status": "resolved",
                    "incident_id": inc_id,
                    "occurrence_id": current_occ_id,
                    "detail": "Incident marked resolved by Grafana notification",
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
                "occurrence_id": occurrence_id,
                "alertname": EXPECTED_ALERT_NAME,
                "status": "diagnosing",
                "write_authorized": False,
                "created_at": time.time(),
            }

        # 7-9. Exception-safe provisioning and read-only diagnosis
        workspace_path = None
        auth_token = None
        try:
            # In default read-only service mode, avoid unnecessary repair workspaces and tokens.
            # Provision only when HTTP repair is explicitly enabled (e.g. for local drill / tests).
            if is_http_repair_allowed():
                try:
                    repo_root = Path(__file__).resolve().parents[1]
                    workspace_path = create_disposable_workspace(repo_root, incident_id)
                except Exception as exc:
                    raise ProvisioningError(f"workspace_creation_failed: {exc}", code="workspace_creation_failed") from exc

                try:
                    auth_token = generate_incident_token(incident_id)
                except Exception as exc:
                    raise ProvisioningError(f"token_generation_failed: {exc}", code="token_generation_failed") from exc

            # Read-only diagnosis execution
            try:
                incident_context = collect_incident_context(PROMETHEUS_URL, LOKI_URL, TEMPO_URL, payload)
                diagnosis_result = get_active_adapter().diagnose(incident_context)
            except Exception as exc:
                raise DiagnosticError(f"diagnosis_failed: {exc}", code="diagnosis_failed") from exc

        except Exception as exc:
            # Cleanup ONLY resources belonging to this failing incident
            if workspace_path:
                try:
                    cleanup_disposable_workspace(workspace_path, incident_id)
                except Exception:
                    pass
            token_file = _get_token_path(incident_id)
            if token_file.exists():
                try:
                    token_file.unlink()
                except OSError:
                    pass

            # Atomic state reset ONLY if this incident is still the active diagnosing incident
            with _lock:
                if _current_incident and _current_incident.get("incident_id") == incident_id:
                    if _current_status == "diagnosing":
                        _current_status = "idle"
                        _current_incident = None

            err_code = (
                getattr(exc, "code", None)
                or ("token_generation_failed" if isinstance(exc, ProvisioningError) and "token" in str(exc)
                    else ("workspace_creation_failed" if isinstance(exc, ProvisioningError) and "workspace" in str(exc)
                          else "diagnosis_failed"))
            )
            return self.send_json(500, {
                "error": err_code,
                "incident_id": incident_id,
                "detail": "Provisioning or diagnosis failed",
            })

        # 10. Update state atomically, respecting mid-diagnosis resolution
        with _lock:
            if not _current_incident or _current_incident.get("incident_id") != incident_id:
                return self.send_json(200, {
                    "status": "discarded",
                    "accepted": False,
                    "detail": "Incident was cleared while diagnosing",
                })

            # Check if resolved notification arrived while diagnosis was in flight
            if _current_status == "resolved" or _current_incident.get("status") == "resolved":
                _current_incident["telemetry"] = incident_context
                _current_incident["diagnosis"] = diagnosis_result
                return self.send_json(200, {
                    "status": "resolved",
                    "accepted": True,
                    "incident_id": incident_id,
                    "occurrence_id": occurrence_id,
                    "detail": "Diagnosis completed after incident was resolved",
                })

            _current_status = "diagnosed"
            _current_incident.update({
                "status": "diagnosed",
                "occurrence_id": occurrence_id,
                "telemetry": incident_context,
                "diagnosis": diagnosis_result,
                "has_correlated_evidence": incident_context.get("has_correlated_evidence", False),
                "workspace_path": str(workspace_path) if workspace_path else None,
                "write_authorized": False,  # STRICT: read-only by default
            })
            _incident_history.append(dict(_current_incident))
            if len(_incident_history) > MAX_INCIDENT_HISTORY:
                _incident_history.pop(0)

        return self.send_json(200, {
            "status": "diagnosed",
            "accepted": True,
            "incident_id": incident_id,
            "occurrence_id": occurrence_id,
            "read_only": True,
            "write_authorized": False,
            "has_correlated_evidence": incident_context.get("has_correlated_evidence", False),
            "diagnosis": diagnosis_result,
        })

    def _handle_authorize_repair(self) -> None:
        """Authorize write-capable remediation with strict authentication and incident binding.
        
        Enforces:
        - Network-facing repair is disabled in normal service mode (returns HTTP 403 repair_disabled).
        - X-Operator-Token header matching single-use incident token.
        - Token verification and state transition executed atomically under lock.
        - Incorrect token attempts do NOT consume legitimate operator tokens.
        - Strict boolean confirm (rejects strings 'false' or non-booleans).
        - Workspace is strictly service-managed (rejects arbitrary client paths).
        - Atomic concurrency guard (at most one active repair).
        """
        global _current_status, _current_incident, _incident_history

        # 0. Check network repair policy
        if not is_http_repair_allowed():
            return self.send_json(403, {
                "error": "repair_disabled",
                "detail": "Network-facing repair authorization is disabled for security. On-call responder is strictly read-only.",
            })

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

        # 4. Atomic incident state check, token verification, and lock acquisition
        with _lock:
            # Token authentication precedes state checks (unauthorized requests rejected with 403)
            if not verify_token_match(incident_id, auth_token):
                return self.send_json(403, {
                    "error": "authorization_denied",
                    "detail": "Invalid or expired operator authorization token",
                })

            if not _current_incident or _current_incident.get("incident_id") != incident_id:
                return self.send_json(404, {"error": "incident_not_found"})

            if _current_status != "diagnosed":
                return self.send_json(409, {
                    "error": "invalid_state",
                    "detail": f"Incident state is '{_current_status}'; repair requires 'diagnosed'",
                })

            # Consume single-use token upon valid state transition
            consume_token(incident_id)

            # Atomic transition to repairing
            _current_status = "repairing"
            _current_incident["status"] = "repairing"
            _current_incident["write_authorized"] = True

        # 5. Resolve service-registered workspace
        registered_ws = get_registered_workspace(incident_id)
        if not registered_ws or not registered_ws.exists():
            with _lock:
                _current_status = "repair_failed"
                _current_incident["status"] = "repair_failed"
            return self.send_json(500, {"error": "workspace_unavailable"})

        # 6. Execute remediation in the registered disposable workspace
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

        # 7. Update final state
        with _lock:
            tests_passed = repair_result.get("tests_passed", False)
            http_verified = repair_result.get("http_verified", False)
            no_defect = repair_result.get("status") == "no_defect_detected"
            success = bool(tests_passed and (http_verified or no_defect))

            # Check if resolution arrived while repairing
            if _current_incident and _current_incident.get("resolved_pending"):
                _current_status = "resolved"
                _current_incident["status"] = "resolved"
            else:
                _current_status = "repaired" if success else "repair_failed"
                if _current_incident:
                    _current_incident["status"] = _current_status

            if _current_incident:
                _current_incident["repair"] = repair_result
                _incident_history.append(dict(_current_incident))
                if len(_incident_history) > MAX_INCIDENT_HISTORY:
                    _incident_history.pop(0)

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
