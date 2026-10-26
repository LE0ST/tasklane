"""Unit tests for incident telemetry collector (collector.py)."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from collector import (
    collect_incident_context,
    normalize_trace_id,
    query_loki_task_5xx_logs,
    query_tempo_span_summary,
    sanitize_text,
)


def test_sanitize_text_redacts_credentials():
    raw_log = (
        "Connected to postgresql://postgres:SecretPassword123@db.example.com:5432/neondb. "
        "Authorization: Bearer my_very_secret_token_123456789. "
        "password: super_secret_pass"
    )
    sanitized = sanitize_text(raw_log)
    assert "SecretPassword123" not in sanitized
    assert "my_very_secret_token_123456789" not in sanitized
    assert "super_secret_pass" not in sanitized
    assert "[REDACTED]" in sanitized


def test_normalize_trace_id():
    valid_hex = "4bf92f3577b34da6a3ce929d0e0e4736"
    assert normalize_trace_id(valid_hex) == valid_hex.lower()
    assert normalize_trace_id(valid_hex.upper()) == valid_hex.lower()

    # Reject all zeros
    assert normalize_trace_id("0" * 32) is None

    # Reject non-32 length
    assert normalize_trace_id("12345") is None

    # Reject non-hex
    assert normalize_trace_id("g" * 32) is None
    assert normalize_trace_id(None) is None


def test_query_loki_task_5xx_logs_filtering():
    dummy_tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    mock_loki_response = {
        "status": "success",
        "data": {
            "result": [
                {
                    "values": [
                        # 1. Matching task 503 log
                        ["1000000000", json.dumps({
                            "route": "/api/tasks",
                            "method": "POST",
                            "status_code": 503,
                            "trace_id": dummy_tid,
                            "duration_ms": 12.3,
                        })],
                        # 2. Non-5xx task log (should be ignored)
                        ["1000000001", json.dumps({
                            "route": "/api/tasks",
                            "method": "POST",
                            "status_code": 201,
                            "trace_id": "11111111111111111111111111111111",
                        })],
                        # 3. Unrelated route 500 log (should be ignored)
                        ["1000000002", json.dumps({
                            "route": "/health",
                            "method": "GET",
                            "status_code": 500,
                            "trace_id": "22222222222222222222222222222222",
                        })],
                    ]
                }
            ]
        }
    }

    with patch("collector.fetch_json", return_value=mock_loki_response):
        events = query_loki_task_5xx_logs("http://mock-loki:3100")
        assert len(events) == 1
        assert events[0]["route"] == "/api/tasks"
        assert events[0]["status_code"] == 503
        assert events[0]["trace_id"] == dummy_tid


def test_query_tempo_span_summary_validation():
    dummy_tid = "4bf92f3577b34da6a3ce929d0e0e4736"

    # 1. Valid SERVER ERROR 503 span
    valid_tempo_data = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": dummy_tid,
                    "kind": 2,  # SERVER
                    "status": {"code": 2},  # ERROR
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": 503}}],
                    "name": "POST /api/tasks",
                }]
            }]
        }]
    }

    with patch("collector.fetch_json", return_value=valid_tempo_data):
        meta = query_tempo_span_summary("http://mock-tempo:3200", dummy_tid)
        assert meta is not None
        assert meta["trace_id"] == dummy_tid
        assert meta["http_status"] == 503
        assert meta["status_code"] == 2

    # 2. UNSET status span must be rejected
    unset_tempo_data = {
        "batches": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": dummy_tid,
                    "kind": 2,
                    "status": {"code": 0},  # UNSET
                    "attributes": [{"key": "http.response.status_code", "value": {"intValue": 503}}],
                }]
            }]
        }]
    }
    with patch("collector.fetch_json", return_value=unset_tempo_data):
        assert query_tempo_span_summary("http://mock-tempo:3200", dummy_tid) is None


def test_collect_incident_context_detects_missing_evidence():
    # Calling collector when offline must not silently succeed
    dummy_payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": "TaskLane5xxRate"}}],
    }
    ctx = collect_incident_context(
        prom_url="http://127.0.0.1:19999",
        loki_url="http://127.0.0.1:19998",
        tempo_url="http://127.0.0.1:19997",
        alert_payload=dummy_payload,
    )
    assert ctx.get("alertname") == "TaskLane5xxRate"
    assert ctx.get("has_correlated_evidence") is False
    assert "loki_structured_task_5xx_log" in ctx.get("missing_evidence")
    assert "tempo_server_error_span" in ctx.get("missing_evidence")
