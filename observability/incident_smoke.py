#!/usr/bin/env python3
"""Issue #3: isolated local user-facing 503 -> alert firing -> alert resolved.

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
GRAFANA = "http://127.0.0.1:3000"
SINK = "http://127.0.0.1:8088"


# ==============================================================================
# Pure Validation Functions (Unit tested & negative tested)
# ==============================================================================

def validate_metric_increase(current_val, baseline_val):
    """Validate that metric has increased strictly above an established baseline.

    A zero-valued series or non-positive value must NEVER pass as evidence of an error.
    """
    if current_val is None or baseline_val is None:
        return False
    try:
        c_val = float(current_val)
        b_val = float(baseline_val)
    except (TypeError, ValueError):
        return False
    # Must be strictly positive and strictly greater than baseline
    if c_val <= 0.0 or c_val <= b_val:
        return False
    return True


def validate_tempo_error_trace(data, expected_trace_id, expected_status_code=503):
    """Validate that Tempo response contains a span with matching trace ID, SERVER kind, HTTP status, and ERROR status.

    Does NOT accept arbitrary nonempty batches or UNSET/OK traces.
    """
    if not isinstance(data, dict):
        return False
    batches = data.get("batches")
    if not isinstance(batches, list) or not batches:
        return False

    clean_expected_tid = expected_trace_id.lower().strip() if expected_trace_id else ""
    if not clean_expected_tid:
        return False

    for batch in batches:
        if not isinstance(batch, dict):
            continue
        for scope_span in batch.get("scopeSpans", []):
            if not isinstance(scope_span, dict):
                continue
            for span in scope_span.get("spans", []):
                if not isinstance(span, dict):
                    continue

                # 1. Trace ID check
                span_tid = str(span.get("traceId", "")).lower().strip()
                if span_tid != clean_expected_tid:
                    continue

                # 2. Kind check: SERVER kind in OTel is 2 or "SPAN_KIND_SERVER"
                kind = span.get("kind")
                if kind not in (2, "SPAN_KIND_SERVER", "server", "Server"):
                    continue

                # 3. Status check: Status code ERROR in OTel is 2 or "STATUS_CODE_ERROR"
                status_obj = span.get("status", {})
                if not isinstance(status_obj, dict):
                    continue
                status_code = status_obj.get("code")
                if status_code not in (2, "STATUS_CODE_ERROR", "ERROR", "error"):
                    continue

                # 4. HTTP response status code metadata check
                attrs = span.get("attributes", [])
                matched_status = False
                if isinstance(attrs, list):
                    for attr in attrs:
                        if not isinstance(attr, dict):
                            continue
                        if attr.get("key") == "http.response.status_code":
                            val_obj = attr.get("value", {})
                            if isinstance(val_obj, dict):
                                val = val_obj.get("intValue") or val_obj.get("stringValue") or val_obj.get("value")
                            else:
                                val = val_obj
                            if val is not None and str(val) == str(expected_status_code):
                                matched_status = True
                                break
                elif isinstance(attrs, dict):
                    if str(attrs.get("http.response.status_code")) == str(expected_status_code):
                        matched_status = True

                if matched_status:
                    return True

    return False


def validate_webhook_recovery(events, alertname="TaskLane5xxRate"):
    """Validate ordered, matching firing then resolved events from the alert sink.

    Rejects resolved-before-firing, missing firing, or empty event history.
    """
    if not isinstance(events, list) or not events:
        return False

    firing_idx = None
    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        if event.get("alertname") == alertname and event.get("status") == "firing":
            firing_idx = idx
            break

    if firing_idx is None:
        return False

    resolved_after_firing = False
    for idx in range(firing_idx + 1, len(events)):
        event = events[idx]
        if isinstance(event, dict) and event.get("alertname") == alertname and event.get("status") == "resolved":
            resolved_after_firing = True
            break

    return resolved_after_firing


def validate_recovery_query(prom_response_data):
    """Confirm that the underlying alert query returns zero after recovery.

    Rejects NoData / empty results or non-zero error rate.
    """
    if not isinstance(prom_response_data, dict):
        return False
    results = prom_response_data.get("data", {}).get("result", [])
    if not results:
        # NoData or missing telemetry must NEVER pass as recovery
        return False
    try:
        val = float(results[0].get("value", [None, None])[1])
        return val == 0.0
    except (IndexError, TypeError, ValueError):
        return False


# ==============================================================================
# Negative Test Suite (Deliberately supplies invalid / false-positive cases)
# ==============================================================================

def run_negative_tests():
    """Execute negative test suite verifying rejection of all false-positive conditions."""
    dummy_tid = "4bf92f3577b34da6a3ce929d0e0e4736"

    # 1. Zero counter values must never pass as evidence of an error
    assert validate_metric_increase(0.0, 0.0) is False, "Zero counter value must fail validation"
    assert validate_metric_increase(0, 0) is False, "Integer 0 counter value must fail validation"
    assert validate_metric_increase(0.0, 1.0) is False, "Zero counter against baseline must fail validation"
    assert validate_metric_increase(1.0, 1.0) is False, "Unchanged counter against baseline must fail validation"
    assert validate_metric_increase(-1.0, 0.0) is False, "Negative counter must fail validation"

    # 2. UNSET-status traces must fail validation
    unset_trace = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": dummy_tid,
                    "kind": 2,
                    "status": {"code": 0},  # UNSET
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(unset_trace, dummy_tid, 503) is False, "UNSET-status trace must fail validation"

    # OK-status traces must also fail validation for error span
    ok_trace = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": dummy_tid,
                    "kind": 2,
                    "status": {"code": 1},  # OK
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(ok_trace, dummy_tid, 503) is False, "OK-status trace must fail validation"

    # Non-empty batches fallback must NOT pass
    empty_spans_batch = {"batches": [{"scopeSpans": []}]}
    assert validate_tempo_error_trace(empty_spans_batch, dummy_tid, 503) is False, "Empty scopeSpans fallback must fail"

    # 3. Wrong trace IDs or wrong HTTP status must fail validation
    wrong_id_trace = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": "99999999999999999999999999999999",
                    "kind": 2,
                    "status": {"code": 2},
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(wrong_id_trace, dummy_tid, 503) is False, "Wrong trace ID must fail validation"

    wrong_status_trace = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": dummy_tid,
                    "kind": 2,
                    "status": {"code": 2},
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "200"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(wrong_status_trace, dummy_tid, 503) is False, "Wrong HTTP status 200 must fail validation"

    wrong_kind_trace = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": dummy_tid,
                    "kind": 1,  # INTERNAL, not SERVER
                    "status": {"code": 2},
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(wrong_kind_trace, dummy_tid, 503) is False, "Wrong span kind must fail validation"

    # 4. Resolved-before-firing webhook histories must fail validation
    resolved_first_history = [
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
    ]
    assert validate_webhook_recovery(resolved_first_history) is False, "Resolved before firing must fail validation"

    only_resolved_history = [
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]
    assert validate_webhook_recovery(only_resolved_history) is False, "Only-resolved history must fail validation"

    empty_history = []
    assert validate_webhook_recovery(empty_history) is False, "Empty webhook history must fail validation"

    # 5. NoData or missing telemetry instead of true recovery must fail validation
    nodata_response = {"status": "success", "data": {"result": []}}
    assert validate_recovery_query(nodata_response) is False, "NoData / empty result must fail recovery validation"

    empty_dict_response = {}
    assert validate_recovery_query(empty_dict_response) is False, "Missing data dict must fail recovery validation"

    nonzero_response = {"status": "success", "data": {"result": [{"value": [1700000000, "1.2"]}]}}
    assert validate_recovery_query(nonzero_response) is False, "Non-zero error rate must fail recovery validation"

    # Positive sanity checks for validators
    valid_error_trace = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": dummy_tid,
                    "kind": 2,
                    "status": {"code": 2},
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(valid_error_trace, dummy_tid, 503) is True
    assert validate_metric_increase(1.0, 0.0) is True
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True
    assert validate_recovery_query({"status": "success", "data": {"result": [{"value": [1700000000, "0.0"]}]}}) is True


# ==============================================================================
# HTTP Client & Polling Utilities
# ==============================================================================

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


def get_metric_scalar(promql):
    url = PROM + "/api/v1/query?query=" + urllib.parse.quote(promql)
    status, data = get_json(url)
    if status != 200 or data.get("status") != "success":
        return None
    results = data.get("data", {}).get("result", [])
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (IndexError, TypeError, ValueError):
        return None


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


def check_grafana_rule_state():
    """Verify Grafana evaluated rule state is normal/inactive where supported."""
    try:
        url = GRAFANA + "/api/prometheus/grafana/api/v1/rules"
        status, data = get_json(url)
        if status != 200 or data.get("status") != "success":
            return None
        groups = data.get("data", {}).get("groups", [])
        for group in groups:
            for rule in group.get("rules", []):
                if rule.get("name") == "TaskLane5xxRate":
                    state = str(rule.get("state", "")).lower()
                    return state
    except Exception:
        pass
    return None


# ==============================================================================
# Main Live Drill
# ==============================================================================

def main():
    # 0. Execute negative tests first to prove false positives are rejected
    run_negative_tests()
    print("PASS: Negative validation tests confirmed rejection of zero counter, UNSET trace, wrong trace ID/status, resolved-before-firing, and NoData.")

    # 1. Health checks
    poll(lambda: get_json(API + "/health")[1].get("status") == "healthy", 90)
    poll(lambda: get_json(SINK + "/events")[0] == 200, 60)
    _, before = get_json(SINK + "/events")
    assert before.get("events") == [], "Alert sink must start empty in isolated CI"

    # 2. Establish a known-good user journey before the drill
    good_status, _ = get_json(API + "/api/tasks", "POST", {"Content-Type": "application/json"}, {"title": "issue3-baseline", "priority": "low"})
    assert good_status == 201

    # 3. Establish known-zero baseline for the aggregate task 5xx counter
    def aggregate_error_counter_at_zero():
        val = get_metric_scalar("tasklane_task_5xx_errors_total")
        return val is not None and val == 0.0

    poll(aggregate_error_counter_at_zero, 75)
    baseline_val = get_metric_scalar("tasklane_task_5xx_errors_total")
    assert baseline_val == 0.0, f"Expected baseline 0.0, got {baseline_val}"

    # Verify alert query evaluates to zero on baseline
    baseline_query_url = PROM + "/api/v1/query?query=" + urllib.parse.quote("sum(increase(tasklane_task_5xx_errors_total[2m]))")
    _, baseline_query_data = get_json(baseline_query_url)
    assert validate_recovery_query(baseline_query_data), "Baseline alert query must evaluate to zero"

    # Give Grafana time to evaluate the known-zero baseline
    time.sleep(12)

    # 4. Inject guarded local 503
    bad_status, bad_body = get_json(API + "/api/tasks", "POST",
        {"Content-Type": "application/json", "x-tasklane-drill": "inject-503"},
        {"title": "issue3-unwritten", "priority": "low"})
    assert bad_status == 503 and bad_body.get("detail") == "Controlled local service-unavailable drill"

    # 5. Confirm no database writes occurred for unwritten task
    status, tasks = get_json(API + "/api/tasks")
    assert status == 200
    assert not any(task.get("title") == "issue3-unwritten" for task in tasks)

    # 6. Verify strictly positive metric increase relative to established zero baseline
    def metric_increased():
        current_val = get_metric_scalar("tasklane_task_5xx_errors_total")
        return validate_metric_increase(current_val, baseline_val)

    poll(metric_increased, 100)

    # Also verify detailed request metrics for investigation
    def detailed_metric_present():
        val = get_metric_scalar('tasklane_http_requests_total{http_route="/api/tasks",http_response_status_code="503"}')
        return val is not None and val > 0.0

    poll(detailed_metric_present, 100)

    # 7. Correlated sanitized Loki log
    trace_id = poll(check_log, 100)

    # 8. Correlated Tempo trace with strict SERVER kind, ERROR status, and 503 metadata (no fallback)
    def tempo_error_span():
        status, data = get_json(TEMPO + "/api/traces/" + trace_id)
        if status != 200:
            return False
        return validate_tempo_error_trace(data, trace_id, 503)

    poll(tempo_error_span, 60)
    print("PASS: Local POST /api/tasks returned 503 without DB writes and with metric, sanitized Loki log, Tempo trace.")

    # 9. Verify real Grafana firing webhook delivery
    def alert_firing_received():
        _, payload = get_json(SINK + "/events")
        return any(e.get("alertname") == "TaskLane5xxRate" and e.get("status") == "firing" for e in payload.get("events", []))

    poll(alert_firing_received, 140)
    print("PASS: Grafana TaskLane5xxRate fired and delivered webhook to isolated local receiver.")

    # 10. After the 2-minute rolling window expires, errors age out; verify recovery
    def alert_resolved_received():
        _, payload = get_json(SINK + "/events")
        return validate_webhook_recovery(payload.get("events", []))

    poll(alert_resolved_received, 215)

    # 11. Confirm that underlying alert query returns zero
    def alert_query_returned_zero():
        query_url = PROM + "/api/v1/query?query=" + urllib.parse.quote("sum(increase(tasklane_task_5xx_errors_total[2m]))")
        st, res_data = get_json(query_url)
        if st != 200:
            return False
        return validate_recovery_query(res_data)

    poll(alert_query_returned_zero, 60)

    # 12. Confirm ordinary API requests still succeed
    post_st, _ = get_json(API + "/api/tasks", "POST", {"Content-Type": "application/json"}, {"title": "issue3-recovery-verified", "priority": "low"})
    assert post_st == 201
    get_st, _ = get_json(API + "/api/tasks")
    assert get_st == 200

    # 13. Where supported, verify Grafana's evaluated rule state is Normal / Inactive
    grafana_state = check_grafana_rule_state()
    if grafana_state is not None:
        assert grafana_state in ("inactive", "normal"), f"Grafana rule state not recovered: {grafana_state}"

    print("PASS: Grafana recovered and delivered resolved notification; no production access.")


if __name__ == "__main__":
    main()
