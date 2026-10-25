#!/usr/bin/env python3
"""Local observability smoke: check cross-signal delivery without exposing secrets."""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://127.0.0.1:8000"
PROM = "http://127.0.0.1:9090"
LOKI = "http://127.0.0.1:3100"
TEMPO = "http://127.0.0.1:3200"
GRAFANA = "http://127.0.0.1:3000"


def request(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=8) as result:
        return json.load(result)


def request_raw(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as result:
            return result.status, json.load(result)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode())
        except Exception:
            body = None
        return exc.code, body


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
    # 1. System health check
    eventually(lambda: request(API + "/health").get("status") == "healthy", 90)

    # 2. Successful task creation & patch
    created = request(API + "/api/tasks", "POST", {"title": "otel-ci-local", "priority": "low"})
    changed = request(API + "/api/tasks/" + str(created["id"]), "PATCH", {"status": "todo"})
    assert changed["status"] == "todo"

    # 3. Exercising client failure path (404 Not Found)
    status_404, _ = request_raw(API + "/api/tasks/999999", "GET")
    assert status_404 == 404, f"Expected 404 for nonexistent task, got {status_404}"

    # 4. Verify Prometheus request metrics for both success (PATCH) and failure (404)
    def metrics_exist():
        q_patch = urllib.parse.quote('tasklane_http_requests_total{http_request_method="PATCH"}')
        res_patch = request(PROM + "/api/v1/query?query=" + q_patch)
        if not (res_patch.get("status") == "success" and bool(res_patch["data"]["result"])):
            return False

        q_404 = urllib.parse.quote('tasklane_http_requests_total{http_response_status_code="404"}')
        res_404 = request(PROM + "/api/v1/query?query=" + q_404)
        if not (res_404.get("status") == "success" and bool(res_404["data"]["result"])):
            return False

        return True

    eventually(metrics_exist, 90)

    # 5. Grafana must expose all provisioned backends, not just start a container.
    eventually(lambda: request(GRAFANA + "/api/health").get("database") == "ok", 60)
    sources = eventually(
        lambda: {entry.get("name") for entry in request(GRAFANA + "/api/datasources")},
        45,
    )
    assert {"Prometheus", "Loki", "Tempo"}.issubset(sources), "Missing Grafana datasource"

    # 6. Verify Loki JSON logs correlation, regex matching and content redaction for PATCH (200)
    trace_id_pattern = re.compile(r'"trace_id"\s*:\s*"([0-9a-f]{32})"')

    def find_patch_log():
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
                    assert "otel-ci-local" not in line and '"title"' not in line, "Sensitive task content leaked to logs"
                    regex_match = trace_id_pattern.search(line)
                    assert regex_match is not None, "Grafana derivedField matcherRegex failed to match Loki log line"
                    extracted_trace_id = regex_match.group(1)
                    trace_id = record.get("trace_id")
                    assert extracted_trace_id == trace_id, "Derived field regex extraction did not match log trace_id"
                    if isinstance(trace_id, str) and len(trace_id) == 32 and trace_id != "0" * 32:
                        return trace_id
        return None

    patch_trace_id = eventually(find_patch_log, 90)

    # 7. Verify Loki JSON log correlation for failure path (404)
    def find_404_log():
        query = urllib.parse.quote('{service_name="tasklane-api"}')
        data = request(LOKI + "/loki/api/v1/query_range?query=" + query + "&limit=100")
        results = data.get("data", {}).get("result", [])
        for stream in results:
            for timestamp, line in stream.get("values", []):
                try:
                    record = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if record.get("method") == "GET" and record.get("route") == "/api/tasks/{task_id}" and record.get("status_code") == 404:
                    regex_match = trace_id_pattern.search(line)
                    assert regex_match is not None, "Grafana matcherRegex failed to match 404 Loki log line"
                    extracted_trace_id = regex_match.group(1)
                    trace_id = record.get("trace_id")
                    assert extracted_trace_id == trace_id
                    if isinstance(trace_id, str) and len(trace_id) == 32 and trace_id != "0" * 32:
                        return trace_id
        return None

    err_404_trace_id = eventually(find_404_log, 90)

    # 8. Verify Tempo traces for both PATCH (200) and 404 failure path
    eventually(lambda: bool(request(TEMPO + "/api/traces/" + patch_trace_id).get("batches")), 60)
    eventually(lambda: bool(request(TEMPO + "/api/traces/" + err_404_trace_id).get("batches")), 60)

    print("PASS: Task create/patch, 404 failure path, request metrics (200 & 404), correlated Loki logs and Tempo traces.")
    print("PASS: Grafana provisioned Prometheus/Loki/Tempo datasources and no task title appears in request log.")
    print("PASS: Grafana derived-field regex successfully extracted trace IDs for both success and 404 error logs.")
    print("Trace IDs verified (redacted from CI output).")


if __name__ == "__main__":
    main()
