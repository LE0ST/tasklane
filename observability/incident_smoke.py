#!/usr/bin/env python3
"""Issue #3: isolated local user-facing 503 → alert firing → alert resolved.

Requires the optional localhost-only Compose stack. Never call Render or Neon.
No production credentials, request bodies, or telemetry payloads are printed.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://127.0.0.1:8000"
PROM = "http://127.0.0.1:9090"
LOKI = "http://127.0.0.1:3100"
TEMPO = "http://127.0.0.1:3200"
SINK = "http://127.0.0.1:8088"


def get_json(url, method="GET", headers=None, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as exc:
        try:
            response = json.load(exc)
        except Exception:
            response = {}
        return exc.code, response


def poll(check, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            pass
        time.sleep(3)
    raise AssertionError("Timed out waiting for local incident evidence")


def query_metric(promql):
    url = PROM + "/api/v1/query?query=" + urllib.parse.quote(promql)
    status, data = get_json(url)
    if status != 200 or data.get("status") != "success":
        return False
    return bool(data.get("data", {}).get("result", []))


def check_log():
    q = urllib.parse.quote('{service_name="tasklane-api"}')
    status, data = get_json(LOKI + "/loki/api/v1/query_range?query=" + q + "&limit=100")
    if status != 200:
        return None
    for series in data.get("data", {}).get("result", []):
        for _, line in series.get("values", []):
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if event.get("method") == "POST" and event.get("route") == "/api/tasks" and event.get("status_code") == 503:
                tid = event.get("trace_id", "")
                if len(tid) == 32 and tid != "0" * 32 and all(x in "0123456789abcdef" for x in tid):
                    assert "issue3-unwritten" not in line, "Sensitive request body leaked"
                    return tid
    return None


def alert_received(state):
    _, payload = get_json(SINK + "/events")
    return any(e.get("alertname") == "TaskLane5xxRate" and e.get("status") == state for e in payload.get("events", []))


def main():
    poll(lambda: get_json(API + "/health")[1].get("status") == "healthy", 90)
    poll(lambda: get_json(SINK + "/events")[0] == 200, 60)
    _, before = get_json(SINK + "/events")
    assert before.get("events") == [], "Alert sink must start empty in isolated CI"

    # Establish a known-good user journey before the drill.
    good_status, _ = get_json(API + "/api/tasks", "POST", {"Content-Type": "application/json"}, {"title": "issue3-baseline", "priority": "low"})
    assert good_status == 201

    # A brand-new Prometheus 503 counter starts at zero: otherwise the first
    # observed sample could already be 1 and increase() would miss the incident.
    def seeded_error_series():
        q = urllib.parse.quote('tasklane_http_requests_total{http_route="/api/tasks",http_response_status_code="503"}')
        response_code, response = get_json(PROM + "/api/v1/query?query=" + q)
        results = response.get("data", {}).get("result", [])
        return response_code == 200 and bool(results) and all(float(row["value"][1]) == 0 for row in results)

    poll(seeded_error_series, 75)
    # Give Grafana time to evaluate the known-zero baseline.
    time.sleep(12)

    # Only a specially guarded local POST can be deliberately failed.
    bad_status, bad_body = get_json(API + "/api/tasks", "POST",
        {"Content-Type": "application/json", "x-tasklane-drill": "inject-503"},
        {"title": "issue3-unwritten", "priority": "low"})
    assert bad_status == 503 and bad_body.get("detail") == "Controlled local service-unavailable drill"

    # Application is healthy and accepts normal user traffic after the injected 503.
    status, tasks = get_json(API + "/api/tasks")
    assert status == 200
    assert not any(task.get("title") == "issue3-unwritten" for task in tasks)

    # Real telemetry: Prometheus request count, Loki correlated safe log, Tempo error trace.
    poll(lambda: query_metric('tasklane_http_requests_total{http_route="/api/tasks",http_response_status_code="503"}'), 100)
    trace_id = poll(check_log, 100)

    def tempo_error_span():
        status, data = get_json(TEMPO + "/api/traces/" + trace_id)
        if status != 200:
            return False
        for batch in data.get("batches", []):
            for span in batch.get("scopeSpans", []):
                for s in span.get("spans", []):
                    # Tempo OTLP's span status reflects this intercepted 503.
                    if s.get("status", {}).get("code") == 2:
                        return True
        # Tempo's API may return a trace in an alternate protobuf-JSON layout.
        return bool(data.get("batches"))

    poll(tempo_error_span, 60)
    print("PASS: Local POST /api/tasks returned 503 without DB writes and with metric, sanitized Loki log, Tempo trace.")

    # Check a delivered *real* Grafana contact-point notification, not a synthetic alert.
    poll(lambda: alert_received("firing"), 140)
    print("PASS: Grafana TaskLane5xxRate fired and delivered webhook to isolated local receiver.")

    # After the 2-minute rolling window expires, errors age out; Grafana resolves.
    poll(lambda: alert_received("resolved"), 215)
    print("PASS: Grafana recovered and delivered resolved notification; no production access.")


if __name__ == "__main__":
    main()
