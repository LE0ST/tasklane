#!/usr/bin/env python3
"""Local observability smoke: check cross-signal delivery without exposing secrets."""
import json
import os
import time
import urllib.parse
import urllib.request

API = "http://127.0.0.1:8000"
PROM = "http://127.0.0.1:9090"
LOKI = "http://127.0.0.1:3100"
TEMPO = "http://127.0.0.1:3200"


def request(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=8) as result:
        return json.load(result)


def eventually(action, timeout=75):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = action()
            if value:
                return value
        except Exception as exc:
            last_error = exc
        time.sleep(3)
    raise RuntimeError("Telemetry verification timed out; last error: " + str(last_error))


def main():
    eventually(lambda: request(API + "/health").get("status") == "healthy", 90)
    created = request(API + "/api/tasks", "POST", {"title": "otel-ci-local", "priority": "low"})
    changed = request(API + "/api/tasks/" + str(created["id"]), "PATCH", {"status": "todo"})
    assert changed["status"] == "todo"

    def metric_exists():
        query = urllib.parse.quote('tasklane_http_requests_total{http_request_method="PATCH"}')
        data = request(PROM + "/api/v1/query?query=" + query)
        return data.get("status") == "success" and bool(data["data"]["result"])

    eventually(metric_exists, 90)

    def find_log():
        query = urllib.parse.quote('{service_name="tasklane-api"}')
        data = request(LOKI + "/loki/api/v1/query_range?query=" + query + "&limit=100")
        results = data.get("data", {}).get("result", [])
        for stream in results:
            for timestamp, line in stream.get("values", []):
                try:
                    record = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if record.get("method") == "PATCH" and record.get("route") == "/api/tasks/{task_id}" and record.get("status_code") == 200:
                    trace_id = record.get("trace_id")
                    if isinstance(trace_id, str) and len(trace_id) == 32 and trace_id != "0" * 32:
                        return trace_id
        return None

    trace_id = eventually(find_log, 90)

    def trace_exists():
        data = request(TEMPO + "/api/traces/" + trace_id)
        return bool(data.get("batches"))

    eventually(trace_exists, 60)
    print("PASS: Task create/patch, request metric, correlated Loki JSON log and Tempo trace.")
    print("Trace ID verified (redacted from CI output).")


if __name__ == "__main__":
    main()
