"""Unit tests for incident telemetry collector (collector.py)."""
from pathlib import Path
import pytest

import sys
IR_DIR = Path(__file__).resolve().parents[1]
if str(IR_DIR) not in sys.path:
    sys.path.insert(0, str(IR_DIR))

from collector import (
    collect_incident_context,
    extract_trace_id_from_log,
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


def test_extract_trace_id_from_log():
    log_line = "2026-10-25 12:00:00 [ERROR] trace_id=4bf92f3577b34da6a3ce929d0e0e4736 Task failed: 500"
    tid = extract_trace_id_from_log(log_line)
    assert tid == "4bf92f3577b34da6a3ce929d0e0e4736"

    # Json formatted log
    json_log = '{"time": "...", "trace_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90", "msg": "failed"}'
    tid2 = extract_trace_id_from_log(json_log)
    assert tid2 == "a1b2c3d4e5f60718293a4b5c6d7e8f90"

    # No trace id
    assert extract_trace_id_from_log("Generic error without id") is None


def test_collect_incident_context_offline_graceful_fallback():
    # Calling collector with non-existent ports must not crash; returns safe empty context
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
    assert ctx.get("sanitized") is True
    assert ctx.get("trace_id") is None
