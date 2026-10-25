"""Negative tests and validator unit tests for observability/incident_smoke.py.

Verifies that zero counter values, UNSET traces, wrong trace IDs/status codes,
resolved-before-firing webhooks, and NoData queries all fail validation.
"""
import math
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OBS_DIR = REPO_ROOT / "observability"
if str(OBS_DIR) not in sys.path:
    sys.path.insert(0, str(OBS_DIR))

from incident_smoke import (
    validate_metric_increase,
    validate_tempo_error_trace,
    validate_webhook_recovery,
    validate_recovery_query,
    run_negative_tests,
)

DUMMY_TID = "4bf92f3577b34da6a3ce929d0e0e4736"


# ==============================================================================
# 1. Metrics validation: zero, nonfinite, negative, or unchanged values fail
# ==============================================================================

def test_metric_validation_rejects_zero_counter_value():
    """A zero-valued counter must never pass as evidence of an error."""
    assert validate_metric_increase(0.0, 0.0) is False
    assert validate_metric_increase(0, 0) is False
    assert validate_metric_increase("0", "0") is False
    assert validate_metric_increase(0.0, 1.0) is False


def test_metric_validation_rejects_stale_or_unchanged_counter():
    """Metrics that do not strictly exceed baseline must fail validation."""
    assert validate_metric_increase(1.0, 1.0) is False
    assert validate_metric_increase(1.0, 2.0) is False
    assert validate_metric_increase(0.0, 0.0) is False


def test_metric_validation_rejects_nan_and_string_nan():
    """NaN values (float or string) must fail validation."""
    assert validate_metric_increase(float("nan"), 0.0) is False
    assert validate_metric_increase("NaN", 0.0) is False
    assert validate_metric_increase("nan", 0.0) is False
    assert validate_metric_increase("NAN", 0.0) is False


def test_metric_validation_rejects_nan_baseline():
    """NaN baseline values must fail validation."""
    assert validate_metric_increase(1.0, float("nan")) is False
    assert validate_metric_increase(1.0, "NaN") is False
    assert validate_metric_increase(1.0, "nan") is False


def test_metric_validation_rejects_positive_and_negative_infinity():
    """Positive and negative infinity must fail validation."""
    assert validate_metric_increase(float("inf"), 0.0) is False
    assert validate_metric_increase(float("-inf"), 0.0) is False
    assert validate_metric_increase(math.inf, 0.0) is False
    assert validate_metric_increase(-math.inf, 0.0) is False
    assert validate_metric_increase("inf", 0.0) is False
    assert validate_metric_increase("-inf", 0.0) is False
    assert validate_metric_increase("Infinity", 0.0) is False
    assert validate_metric_increase("-Infinity", 0.0) is False
    assert validate_metric_increase(1.0, float("inf")) is False
    assert validate_metric_increase(1.0, float("-inf")) is False


def test_metric_validation_rejects_negative_values():
    """Negative counter or baseline values must fail validation."""
    assert validate_metric_increase(-1.0, 0.0) is False
    assert validate_metric_increase(1.0, -1.0) is False
    assert validate_metric_increase(-1.0, -2.0) is False
    assert validate_metric_increase("-0.1", 0.0) is False


def test_metric_validation_rejects_missing_or_malformed_values():
    """None, empty string, non-numeric strings, and unexpected types fail validation."""
    assert validate_metric_increase(None, 0.0) is False
    assert validate_metric_increase(1.0, None) is False
    assert validate_metric_increase(None, None) is False
    assert validate_metric_increase("", 0.0) is False
    assert validate_metric_increase("invalid", 0.0) is False
    assert validate_metric_increase(1.0, "invalid") is False
    assert validate_metric_increase([], 0.0) is False
    assert validate_metric_increase({}, 0.0) is False


def test_metric_validation_preserves_valid_0_to_1():
    """Valid 0 -> 1 increase and positive increases above baseline pass validation."""
    assert validate_metric_increase(1.0, 0.0) is True
    assert validate_metric_increase(1, 0) is True
    assert validate_metric_increase("1.0", "0.0") is True
    assert validate_metric_increase(2.0, 1.0) is True
    assert validate_metric_increase(5.5, 2.3) is True


# ==============================================================================
# 2. Tempo validation: UNSET traces, wrong trace ID, wrong status, no fallback
# ==============================================================================

def test_tempo_validation_rejects_unset_status_trace():
    """Traces with UNSET status code (0) must fail validation."""
    trace_data = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": DUMMY_TID,
                    "kind": 2,
                    "status": {"code": 0},  # UNSET
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(trace_data, DUMMY_TID, 503) is False


def test_tempo_validation_rejects_ok_status_trace():
    """Traces with OK status code (1) must fail validation."""
    trace_data = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": DUMMY_TID,
                    "kind": 2,
                    "status": {"code": 1},  # OK
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(trace_data, DUMMY_TID, 503) is False


def test_tempo_validation_rejects_wrong_trace_id():
    """Traces with mismatched trace ID must fail validation."""
    trace_data = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": "11111111111111111111111111111111",
                    "kind": 2,
                    "status": {"code": 2},
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(trace_data, DUMMY_TID, 503) is False


def test_tempo_validation_rejects_wrong_http_status_code():
    """Traces with wrong HTTP response status code (e.g. 200 or 404) must fail validation."""
    for wrong_code in ["200", "404", "500"]:
        trace_data = {
            "batches": [{
                "scopeSpans": [{
                    "spans": [{
                        "traceId": DUMMY_TID,
                        "kind": 2,
                        "status": {"code": 2},
                        "attributes": [{"key": "http.response.status_code", "value": {"intValue": wrong_code}}]
                    }]
                }]
            }]
        }
        assert validate_tempo_error_trace(trace_data, DUMMY_TID, 503) is False


def test_tempo_validation_rejects_wrong_span_kind():
    """Spans that are not SERVER kind (e.g. INTERNAL=1 or CLIENT=3) must fail validation."""
    trace_data = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": DUMMY_TID,
                    "kind": 1,  # INTERNAL
                    "status": {"code": 2},
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(trace_data, DUMMY_TID, 503) is False


def test_tempo_validation_eliminates_nonempty_batches_fallback():
    """Arbitrary nonempty batches without matching error spans must fail validation."""
    assert validate_tempo_error_trace({"batches": [{"scopeSpans": []}]}, DUMMY_TID, 503) is False
    assert validate_tempo_error_trace({"batches": [{}]}, DUMMY_TID, 503) is False
    assert validate_tempo_error_trace({}, DUMMY_TID, 503) is False
    assert validate_tempo_error_trace(None, DUMMY_TID, 503) is False


def test_tempo_validation_accepts_verified_otlp_error_layout():
    """Fully matching server error span with 503 metadata passes validation."""
    trace_data = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": DUMMY_TID,
                    "kind": 2,
                    "status": {"code": 2},
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": "503"}}]
                }]
            }]
        }]
    }
    assert validate_tempo_error_trace(trace_data, DUMMY_TID, 503) is True


def test_tempo_validation_accepts_base64_encoded_trace_id():
    """Tempo protojson returns byte arrays as base64 strings; must normalize and match."""
    import base64
    b64_tid = base64.b64encode(bytes.fromhex(DUMMY_TID)).decode()
    trace_data = {
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
    assert validate_tempo_error_trace(trace_data, DUMMY_TID, 503) is True


# ==============================================================================
# 3. Webhook recovery validation: lifecycle, duplicates, and anomaly rejection
# ==============================================================================

def test_webhook_validation_accepts_matching_firing_then_resolved():
    """Ordered firing followed by resolved event passes validation."""
    history = [
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]
    assert validate_webhook_recovery(history) is True


def test_webhook_validation_accepts_valid_duplicate_notifications():
    """Duplicate notifications where terminal state is resolved pass validation."""
    # Duplicate firing before resolved
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True

    # Firing followed by duplicate resolved
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True

    # Duplicate firing followed by duplicate resolved
    assert validate_webhook_recovery([
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]) is True


def test_webhook_validation_rejects_resolved_firing_resolved():
    """Histories starting with resolved (resolved -> firing -> resolved) must fail validation."""
    history = [
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]
    assert validate_webhook_recovery(history) is False


def test_webhook_validation_rejects_firing_resolved_firing():
    """Histories with a firing event after previous resolution must fail validation."""
    history = [
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
    ]
    assert validate_webhook_recovery(history) is False


def test_webhook_validation_rejects_resolved_firing():
    """Resolved arriving before firing (resolved -> firing) must fail validation."""
    history = [
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
    ]
    assert validate_webhook_recovery(history) is False


def test_webhook_validation_rejects_single_resolved():
    """History containing only resolved event without prior firing must fail validation."""
    assert validate_webhook_recovery([{"alertname": "TaskLane5xxRate", "status": "resolved"}]) is False


def test_webhook_validation_rejects_single_firing():
    """History containing only firing event without terminal resolution must fail validation."""
    assert validate_webhook_recovery([{"alertname": "TaskLane5xxRate", "status": "firing"}]) is False


def test_webhook_validation_rejects_empty_or_malformed_histories():
    """Empty list, None, non-list, or malformed alert payloads fail validation."""
    assert validate_webhook_recovery([]) is False
    assert validate_webhook_recovery(None) is False
    assert validate_webhook_recovery("firing") is False
    assert validate_webhook_recovery([{"alertname": "TaskLane5xxRate", "status": "unknown"}]) is False
    assert validate_webhook_recovery([{"alertname": "TaskLane5xxRate"}]) is False
    assert validate_webhook_recovery([123, "invalid"]) is False


def test_webhook_validation_handles_unrelated_alerts_correctly():
    """Unrelated alerts are ignored without concealing invalid matching transitions."""
    # Unrelated alerts with valid matching transition
    valid_with_unrelated = [
        {"alertname": "UnrelatedAlert", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "UnrelatedAlert", "status": "firing"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
    ]
    assert validate_webhook_recovery(valid_with_unrelated) is True

    # Unrelated alerts interspersed with invalid matching transition (firing after resolved)
    invalid_with_unrelated = [
        {"alertname": "TaskLane5xxRate", "status": "firing"},
        {"alertname": "UnrelatedAlert", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "resolved"},
        {"alertname": "TaskLane5xxRate", "status": "firing"},
    ]
    assert validate_webhook_recovery(invalid_with_unrelated) is False


# ==============================================================================
# 4. Recovery query validation: NoData or missing telemetry fails
# ==============================================================================

def test_recovery_query_rejects_nodata_or_missing_telemetry():
    """NoData / empty result from Prometheus must fail recovery validation."""
    assert validate_recovery_query({"status": "success", "data": {"result": []}}) is False
    assert validate_recovery_query({}) is False
    assert validate_recovery_query(None) is False


def test_recovery_query_rejects_nonzero_error_rate():
    """Non-zero query value during recovery window must fail validation."""
    assert validate_recovery_query({"status": "success", "data": {"result": [{"value": [1000, "1.0"]}]}}) is False
    assert validate_recovery_query({"status": "success", "data": {"result": [{"value": [1000, "0.1"]}]}}) is False


def test_recovery_query_accepts_zero():
    """Prometheus query returning exactly 0.0 passes recovery validation."""
    assert validate_recovery_query({"status": "success", "data": {"result": [{"value": [1000, "0.0"]}]}}) is True


def test_embedded_negative_suite_runs():
    """Confirm that the self-contained run_negative_tests() function completes without error."""
    run_negative_tests()
