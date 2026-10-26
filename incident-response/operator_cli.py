"""TaskLane Issue #4: Dedicated Local Operator CLI.

Provides a secure, out-of-band operator control path for authorized remediation.

CONTAINER VS HOST EXECUTION BOUNDARY:
- When TaskLane on-call responder runs inside Docker (e.g. docker-compose.oncall.yml),
  authorization tokens are stored strictly within the container's private filesystem
  (/tmp/tasklane_oncall_service). Host processes cannot access container tokens.
  Run the operator CLI inside the container via:
    docker exec tasklane-oncall-responder python operator_cli.py authorize --incident-id <ID> --confirm
- When running standalone on the local machine (e.g. incident drill):
  Run directly via:
    python incident-response/operator_cli.py authorize --incident-id <ID> --confirm

Enforces:
- Local-only execution on the host/container where responder is running.
- Reads single-use authorization tokens from protected local storage.
- Requires explicit incident ID and boolean --confirm flag.
- Invokes responder via authenticated POST /authorize-repair with X-Operator-Token.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from config import DISPOSABLE_SERVICE_DIR, RESPONDER_HOST, RESPONDER_PORT


def get_token_for_incident(incident_id: str) -> Optional[str]:
    """Read the local single-use token for an incident."""
    token_file = DISPOSABLE_SERVICE_DIR / f"auth_{incident_id}.token"
    if not token_file.exists():
        return None
    return token_file.read_text(encoding="utf-8").strip()


def authorize_repair(
    incident_id: str,
    responder_url: Optional[str] = None,
    confirm: bool = True,
) -> Dict[str, Any]:
    """Authorize repair using the local single-use incident token."""
    if responder_url is None:
        responder_url = f"http://127.0.0.1:{RESPONDER_PORT}"

    token = get_token_for_incident(incident_id)
    if not token:
        return {
            "error": "token_not_found",
            "detail": f"No single-use authorization token found for incident '{incident_id}'. Has the alert fired and completed diagnosis?",
        }

    url = f"{responder_url.rstrip('/')}/authorize-repair"
    payload = {
        "incident_id": incident_id,
        "confirm": confirm,
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Operator-Token": token,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as err:
        try:
            err_data = json.loads(err.read().decode("utf-8"))
            return {"http_status": err.code, **err_data}
        except Exception:
            return {"http_status": err.code, "error": "http_error", "detail": str(err)}
    except Exception as exc:
        return {"error": "connection_error", "detail": "Failed to connect to responder"}


def query_status(responder_url: Optional[str] = None) -> Dict[str, Any]:
    """Query current responder status."""
    if responder_url is None:
        responder_url = f"http://127.0.0.1:{RESPONDER_PORT}"

    url = f"{responder_url.rstrip('/')}/status"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": "connection_error", "detail": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="TaskLane On-Call Operator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status command
    status_parser = subparsers.add_parser("status", help="Query responder status")
    status_parser.add_argument("--url", default=None, help="Responder URL")

    # authorize command
    auth_parser = subparsers.add_parser("authorize", help="Authorize remediation for an incident")
    auth_parser.add_argument("--incident-id", required=True, help="Incident ID to authorize")
    auth_parser.add_argument("--confirm", action="store_true", required=True, help="Explicit confirmation")
    auth_parser.add_argument("--url", default=None, help="Responder URL")

    args = parser.parse_args()

    if args.command == "status":
        status = query_status(args.url)
        print(json.dumps(status, indent=2))
        return 0

    if args.command == "authorize":
        if not args.confirm:
            print("ERROR: --confirm is required for repair authorization", file=sys.stderr)
            return 1
        result = authorize_repair(args.incident_id, responder_url=args.url, confirm=True)
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") == "repaired" else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
