"""Tests for safe default and opt-in configuration of telemetry."""
import logging
from unittest.mock import patch
from fastapi import FastAPI

from telemetry import enable_telemetry


def test_telemetry_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TASKLANE_OTEL_ENABLED", raising=False)
    app = FastAPI()
    assert enable_telemetry(app) is False
    assert not app.user_middleware


def test_telemetry_requires_valid_endpoint(monkeypatch):
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    monkeypatch.delenv("TASKLANE_OTEL_ENDPOINT", raising=False)
    app = FastAPI()
    with patch.dict("sys.modules", {"opentelemetry": None}):
        assert enable_telemetry(app) is False
    assert not app.user_middleware


def test_disabled_telemetry_never_logs_payload(monkeypatch, caplog):
    monkeypatch.delenv("TASKLANE_OTEL_ENABLED", raising=False)
    with caplog.at_level(logging.INFO):
        assert enable_telemetry(FastAPI()) is False
    text = caplog.text.lower()
    assert "database_url" not in text
    assert "password" not in text
