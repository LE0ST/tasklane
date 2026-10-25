#!/usr/bin/env python3
"""Issue #3: isolated local user-facing 503 -> alert firing -> alert resolved.

Requires the optional localhost-only Compose stack. Never call Render or Neon.
No production credentials, request bodies, or telemetry payloads are printed.
"""
import json
import math
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

    A zero-valued series, nonfinite value (NaN, inf), or negative value must NEVER pass as evidence of an error.
    Requirements:
    - Both current_val and baseline_val must be convertible to float and finite (math.isfinite).
    - baseline_val must be nonnegative (b_val >= 0.0).
    - current_val must be nonnegative and strictly positive (c_val > 0.0).
    - current_val must be strictly greater than baseline_val (c_val > b_val).
    """
    if current_val is None or baseline_val is None:
        return False
    try:
        c_val = float(current_val)
        b_val = float(baseline_val)
    except (TypeError, ValueError):
        return False

    # Reject NaN, positive infinity, and negative infinity
    if not (math.isfinite(c_val) and math.isfinite(b_val)):
        return False

    # Reject negative values
    if c_val < 0.0 or b_val < 0.0:
        return False

    # Must be strictly positive and strictly greater than baseline
    if c_val <= 0.0 or c_val <= b_val:
        return False

    return True


def normalize_trace_id(val):
    """Normalize hex or base64 binary trace ID into a 32-character lowercase hex string."""
    if not val:
        return ""
    s = str(val).strip()
    if len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s):
        return s.lower()
    try:
        import base64
        raw = base64.b64decode(s)
        h = raw.hex()
        if len(h) == 32:
            return h.lower()
    except Exception:
        pass
    return s.lower()


def validate_tempo_error_trace(data, expected_trace_id, expected_status_code=503):
    """Validate that Tempo response contains a span with matching trace ID, SERVER kind, HTTP status, and ERROR status.

    Supports verified Tempo response layouts (hex or base64 trace IDs, scopeSpans or instrumentationLibrarySpans).
    Does NOT accept arbitrary nonempty batches or UNSET/OK traces.
    """
    if not isinstance(data, dict):
        return False
    batches = data.get("batches") or data.get("resourceSpans")
    if isinstance(data.get("trace"), dict):
        batches = data["trace"].get("resourceSpans") or data["trace"].get("batches") or batches
    if not isinstance(batches, list) or not batches:
        return False

    clean_expected_tid = normalize_trace_id(expected_trace_id)
    if not clean_expected_tid:
        return False

    for batch in batches:
        if not isinstance(batch, dict):
            continue
        scope_spans = batch.get("scopeSpans") or batch.get("instrumentationLibrarySpans") or []
        for scope_span in scope_spans:
            if not isinstance(scope_span, dict):
                continue
            for span in scope_span.get("spans", []):
                if not isinstance(span, dict):
                    continue

                # 1. Trace ID check (supports both hex and base64 byte encoding from Tempo)
                span_tid = normalize_trace_id(span.get("traceId"))
                if span_tid != clean_expected_tid:
                    continue

                # 2. Kind check: SERVER kind in OTel is 2 or "SPAN_KIND_SERVER"
                kind = span.get("kind")
                if kind not in (2, "2", "SPAN_KIND_SERVER", "server", "Server"):
                    continue

                # 3. Status check: Status code ERROR in OTel is 2 or "STATUS_CODE_ERROR"
                status_obj = span.get("status", {})
                if not isinstance(status_obj, dict):
                    continue
                code = status_obj.get("code")
                if code not in (2, "2", "STATUS_CODE_ERROR", "ERROR", "error"):
                    continue

                # 4. HTTP response status code metadata check
                attrs = span.get("attributes", [])
                matched_status = False
                if isinstance(attrs, list):
                    for attr in attrs:
                        if not isinstance(attr, dict):
                            continue
                        if attr.get("key") in ("http.response.status_code", "http.status_code"):
                            val_obj = attr.get("value", {})
                            if isinstance(val_obj, dict):
                                val = val_obj.get("intValue") or val_obj.get("stringValue") or val_obj.get("value")
                            else:
                                val = val_obj
                            if val is not None and str(val) == str(expected_status_code):
                                matched_status = True
                                break
                elif isinstance(attrs, dict):
                    val = attrs.get("http.response.status_code") or attrs.get("http.status_code")
                    if val is not None and str(val) == str(expected_status_code):
                        matched_status = True

                if matched_status:
                    return True

    return False


def validate_webhook_recovery(events, alertname="TaskLane5xxRate"):
    """Validate complete event history for the matching alert through terminal resolution.

    Requirements:
    1. Filter the event list to matching entries for `alertname` with a valid dict structure.
       Unrelated alerts are ignored without concealing invalid matching transitions.
    2. Event history must contain at least one matching event. Empty, non-list, or missing
       histories are rejected.
    3. The matching sequence must start with 'firing'. Histories starting with a matching
       'resolved' (e.g. resolved -> firing -> resolved or resolved -> firing) are rejected.
    4. The terminal matching state must be 'resolved'. A final matching 'firing' after
       previous resolution or an ongoing firing state is rejected.
    5. No 'firing' transition is permitted after any 'resolved' event has been observed
       (strictly enforces genuine firing followed by terminal resolution without flapping).
    6. Duplicate notifications are handled consistently: consecutive 'firing' events
       (e.g. initial notification + resend) or consecutive 'resolved' events (e.g. multiple
       resolved notifications) are permitted as long as all firing notifications precede all
       resolved notifications and the terminal state is 'resolved'.
    """
    if not isinstance(events, list) or not events:
        return False

    matching_statuses = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("alertname") == alertname:
            status = event.get("status")
            if status not in ("firing", "resolved"):
                # Malformed status for matching alert
                return False
            matching_statuses.append(status)

    if not matching_statuses:
        return False

    # Must start with 'firing' (rejects histories starting with matching 'resolved')
    if matching_statuses[0] != "firing":
        return False

    # Terminal matching state must be 'resolved' (rejects final matching 'firing')
    if matching_statuses[-1] != "resolved":
        return False

    # Enforce that all firing events precede all resolved events.
    # No firing event can appear after any resolved event has occurred.
    has_resolved = False
    for st in matching_statuses:
        if st == "resolved":
            has_resolved = True
        elif st == "firing":
            if has_resolved:
                return False

    return True


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

    # 1. Metric increase validation: reject nonfinite, negative, zero, or unchanged counters
    assert validate_metric_increase(0.0, 0.0) is False, "Zero counter value must fail validation"
    assert validate_metric_increase(0, 0) is False, "Integer 0 counter value must fail validation"
    assert validate_metric_increase("0", "0") is False, "String '0' counter value must fail validation"
    assert validate_metric_increase(0.0, 1.0) is False, "Zero counter against baseline must fail validation"
    assert validate_metric_increase(1.0, 1.0) is False, "Unchanged counter against baseline must fail validation"
    assert validate_metric_increase(-1.0, 0.0) is False, "Negative counter must fail validation"
    assert validate_metric_increase(1.0, -1.0) is False, "Negative baseline must fail validation"
    assert validate_metric_increase(-1.0, -2.0) is False, "Negative values must fail validation"
    # Reject NaN and 'NaN'
    assert validate_metric_increase(float("nan"), 0.0) is False, "float NaN must fail validation"
    assert validate_metric_increase("NaN", 0.0) is False, "String 'NaN' must fail validation"
    assert validate_metric_increase("nan", 0.0) is False, "String 'nan' must fail validation"
    assert validate_metric_increase(1.0, float("nan")) is False, "NaN baseline must fail validation"
    assert validate_metric_increase(1.0, "NaN") is False, "String 'NaN' baseline must fail validation"
    # Reject positive and negative infinity
    assert validate_metric_increase(float("inf"), 0.0) is False, "float inf must fail validation"
    assert validate_metric_increase(float("-inf"), 0.0) is False, "float -inf must fail validation"
    assert validate_metric_increase(math.inf, 0.0) is False, "math.inf must fail validation"
    assert validate_metric_increase(-math.inf, 0.0) is False, "-math.inf must fail validation"
    assert validate_metric_increase("inf", 0.0) is False, "String 'inf' must fail validation"
    assert validate_metric_increase("-inf", 0.0) is False, "String '-inf' must fail validation"
    assert validate_metric_increase("Infinity", 0.0) is False, "String 'Infinity' must fail validation"
    assert validate_metric_increase(1.0, float("inf")) is False, "Infinity baseline must fail validation"
    # Reject missing or malformed values
    assert validate_metric_increase(None, 0.0) is False, "None current must fail validation"
    assert validate_metric_increase(1.0, None) is False, "None baseline must fail validation"
    assert validate_metric_increase("", 0.0) is False, "Empty string must fail validation"
    assert validate_metric_increase("invalid", 0.0) is False, "Invalid string must fail validation"
    assert validate_metric_increase([], 0.0) is False, "List type must fail validation"
    # Preserve valid 0 -> 1 case
    assert validate_metric_increase(1.0, 0.0) is True, "Valid 0 -> 1 increase must pass"
    assert validate_metric_increase(1, 0) is True, "Integer 0 -> 1 increase must pass"
    assert validate_metric_increase("1.0", "0.0") is True, "String '0.0' -> '1.0' increase must pass"

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

    # 4. Webhook recovery validation: strict lifecycle, duplicate handling, and anomaly rejection
    # FAIL: resolved-first histories
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is False, "resolved -> firing -> resolved must fail validation"

    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
    ]) is False, "resolved -> firing must fail validation"

    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is False, "only-resolved history must fail validation"

    # FAIL: firing-after-resolved histories
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
    ]) is False, "firing -> resolved -> firing must fail validation"

    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
    ]) is False, "only-firing history must fail validation"

    # FAIL: empty, non-list, or malformed histories
    assert validate_webhook_recovery([]) is False, "Empty history must fail validation"
    assert validate_webhook_recovery(None) is False, "None history must fail validation"
    assert validate_webhook_recovery([{"alertname": "TaskLane5xxRate", "status": "unknown"}]) is False, "Unknown status must fail validation"
    assert validate_webhook_recovery([{"alertname": "TaskLane5xxRate"}]) is False, "Missing status must fail validation"
    assert validate_webhook_recovery([123, "malformed"]) is False, "Non-dict entries must fail validation"

    # FAIL: unrelated alerts concealing invalid matching transitions
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "UnrelatedAlert", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
    ]) is False, "Unrelated alert must not conceal invalid matching firing-after-resolved"

    # PASS: standard firing -> resolved
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True, "Standard firing -> resolved must pass"

    # PASS: valid duplicate notifications where terminal state is resolved
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True, "Duplicate firing before resolved must pass"

    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True, "Firing followed by duplicate resolved must pass"

    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True, "Duplicate firing followed by duplicate resolved must pass"

    # PASS: unrelated alerts ignored when matching transitions are valid
    assert validate_webhook_recovery([
        {"alertname": "UnrelatedAlert", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "UnrelatedAlert", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True, "Unrelated alerts must be ignored when matching transitions are valid"

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

    # Tempo base64-encoded trace ID format must be supported
    import base64
    b64_tid = base64.b64encode(bytes.fromhex(dummy_tid)).decode()
    b64_valid_trace = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": b64_tid,
                    "kind": 2,
                    "status": {"code": 2},
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(b64_valid_trace, dummy_tid, 503) is True, "Base64 Tempo trace ID must be supported"
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

    # Poll until Prometheus has multiple samples so increase() evaluates to zero
    def baseline_alert_query_evaluates_to_zero():
        query_url = PROM + "/api/v1/query?query=" + urllib.parse.quote("sum(increase(tasklane_task_5xx_errors_total[2m]))")
        st, data = get_json(query_url)
        if st != 200:
            return False
        return validate_recovery_query(data)

    poll(baseline_alert_query_evaluates_to_zero, 60)

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
