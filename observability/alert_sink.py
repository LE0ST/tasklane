"""Memory-only, local-only webhook sink. Never executes incoming instructions.

This is a disposable evidence receiver, *not* an autonomous on-call agent.
It records only allowlisted alert states and names, discarding the full payload.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_events = []
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Avoid printing untrusted Grafana payloads or identifiers.
        return

    def send_json(self, code, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/events":
            return self.send_json(404, {"error": "not_found"})
        with _lock:
            snapshot = list(_events)
        return self.send_json(200, {"events": snapshot})

    def do_POST(self):
        if self.path != "/alerts":
            return self.send_json(404, {"error": "not_found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 2 or size > 65536:
                return self.send_json(413, {"error": "size_limit"})
            message = json.loads(self.rfile.read(size))
            if not isinstance(message, dict):
                return self.send_json(400, {"error": "invalid"})
            status = message.get("status")
            if status not in ("firing", "resolved"):
                return self.send_json(400, {"error": "unsupported_status"})
            alerts = message.get("alerts")
            if not isinstance(alerts, list):
                return self.send_json(400, {"error": "missing_alerts"})
            accepted = False
            for item in alerts[:5]:
                if not isinstance(item, dict):
                    continue
                labels = item.get("labels", {})
                if not isinstance(labels, dict) or labels.get("alertname") != "TaskLane5xxRate":
                    continue
                item_status = item.get("status")
                if item_status not in ("firing", "resolved"):
                    continue
                with _lock:
                    _events.append({"alertname": "TaskLane5xxRate", "status": item_status})
                    del _events[:-25]
                accepted = True
            self.send_json(200, {"accepted": accepted})
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid_json"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
