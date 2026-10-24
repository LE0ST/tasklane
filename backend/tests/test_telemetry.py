"""Comprehensive tests for optional OpenTelemetry request instrumentation and isolation."""
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi import FastAPI, Response, status
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from telemetry import enable_telemetry


def _create_test_app():
    """Create a standalone FastAPI app with sample routes matching TaskLane templates."""
    app = FastAPI()

    tasks_db = {}
    task_id_counter = [1]

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    @app.get("/api/tasks")
    def list_tasks():
        return list(tasks_db.values())

    @app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
    def create_task(payload: dict):
        if payload.get("priority") not in {"low", "medium", "high"}:
            return Response(
                content=json.dumps({"detail": "Invalid priority"}),
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                media_type="application/json",
            )
        tid = task_id_counter[0]
        task_id_counter[0] += 1
        record = {"id": tid, "title": payload.get("title", ""), "status": "backlog", "priority": payload.get("priority")}
        tasks_db[tid] = record
        return record

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int):
        if task_id not in tasks_db:
            return Response(
                content=json.dumps({"detail": f"Task {task_id} not found"}),
                status_code=status.HTTP_404_NOT_FOUND,
                media_type="application/json",
            )
        return tasks_db[task_id]

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: int, payload: dict):
        if task_id not in tasks_db:
            return Response(
                content=json.dumps({"detail": f"Task {task_id} not found"}),
                status_code=status.HTTP_404_NOT_FOUND,
                media_type="application/json",
            )
        tasks_db[task_id].update(payload)
        return tasks_db[task_id]

    @app.get("/api/fail-unhandled")
    def fail_unhandled():
        raise RuntimeError("SYNTHETIC_UNHANDLED_EXCEPTION_SECRET_TOKEN_99999")

    @app.get("/api/fail-handled-500")
    def fail_handled():
        return Response(
            content=json.dumps({"detail": "Internal server error occurred"}),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            media_type="application/json",
        )

    return app


# ==============================================================================
# 1. Safe default & configuration validation tests
# ==============================================================================


def test_telemetry_disabled_by_default(monkeypatch, caplog):
    monkeypatch.delenv("TASKLANE_OTEL_ENABLED", raising=False)
    app = _create_test_app()
    with caplog.at_level(logging.INFO):
        assert enable_telemetry(app) is False
    assert not app.user_middleware

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}

    post_resp = client.post("/api/tasks", json={"title": "test", "priority": "low"})
    assert post_resp.status_code == 201

    text = caplog.text.lower()
    assert "trace_id" not in text
    assert "tasklane.telemetry" not in text


def test_telemetry_sdk_missing_fails_open(monkeypatch):
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    monkeypatch.setenv("TASKLANE_OTEL_ENDPOINT", "http://127.0.0.1:4318")
    app = _create_test_app()
    with patch.dict("sys.modules", {"opentelemetry": None, "opentelemetry.trace": None}):
        assert enable_telemetry(app) is False
    assert not app.user_middleware

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200


@pytest.mark.parametrize("invalid_endpoint", [
    "",
    "   ",
    "ftp://localhost:4318",
    "localhost:4318",
    "otel-collector:4318",
])
def test_telemetry_endpoint_validation_with_sdk_available(monkeypatch, caplog, invalid_endpoint):
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    if invalid_endpoint == "":
        monkeypatch.delenv("TASKLANE_OTEL_ENDPOINT", raising=False)
    else:
        monkeypatch.setenv("TASKLANE_OTEL_ENDPOINT", invalid_endpoint)
    app = _create_test_app()
    with caplog.at_level(logging.WARNING):
        assert enable_telemetry(app) is False
    assert not app.user_middleware
    assert any("TASKLANE_OTEL_ENDPOINT is not configured" in rec.message for rec in caplog.records)


# ==============================================================================
# 2. Enabled mode: in-memory span & metric assertions, redaction & correlation
# ==============================================================================


def test_telemetry_enabled_in_memory_post_and_patch_correlation(monkeypatch, caplog):
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    app = _create_test_app()

    resource = Resource.create({"service.name": "tasklane-test", "deployment.environment": "test"})
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

    with caplog.at_level(logging.INFO, logger="tasklane.telemetry"):
        assert enable_telemetry(app, tracer_provider=tracer_provider, meter_provider=meter_provider) is True

        client = TestClient(app)

        # 1. POST request with sensitive task content
        sensitive_title = "TOP_SECRET_AIRPLANE_BLUEPRINT_99"
        post_resp = client.post("/api/tasks", json={"title": sensitive_title, "priority": "high"})
        assert post_resp.status_code == 201
        task_id = post_resp.json()["id"]

        # 2. PATCH request
        patch_resp = client.patch(f"/api/tasks/{task_id}", json={"status": "in_progress"})
        assert patch_resp.status_code == 200

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 2

        # Check POST span
        post_span = spans[0]
        assert post_span.name == "HTTP POST"
        assert post_span.kind == SpanKind.SERVER
        assert post_span.status.status_code == StatusCode.UNSET
        assert post_span.attributes["http.request.method"] == "POST"
        assert post_span.attributes["http.route"] == "/api/tasks"
        assert post_span.attributes["http.response.status_code"] == 201
        assert sensitive_title not in post_span.attributes.values()
        assert len(post_span.events) == 0

        # Check PATCH span
        patch_span = spans[1]
        assert patch_span.name == "HTTP PATCH"
        assert patch_span.kind == SpanKind.SERVER
        assert patch_span.status.status_code == StatusCode.UNSET
        assert patch_span.attributes["http.request.method"] == "PATCH"
        assert patch_span.attributes["http.route"] == "/api/tasks/{task_id}"
        assert patch_span.attributes["http.response.status_code"] == 200
        assert len(patch_span.events) == 0

        # Check correlated logs
        log_records = [r for r in caplog.records if r.name == "tasklane.telemetry"]
        assert len(log_records) == 2

        post_log = json.loads(log_records[0].message)
        assert post_log["event"] == "http_request"
        assert post_log["method"] == "POST"
        assert post_log["route"] == "/api/tasks"
        assert post_log["status_code"] == 201
        assert post_log["trace_id"] == format(post_span.context.trace_id, "032x")
        assert post_log["span_id"] == format(post_span.context.span_id, "016x")
        assert sensitive_title not in log_records[0].message

        patch_log = json.loads(log_records[1].message)
        assert patch_log["event"] == "http_request"
        assert patch_log["method"] == "PATCH"
        assert patch_log["route"] == "/api/tasks/{task_id}"
        assert patch_log["status_code"] == 200
        assert patch_log["trace_id"] == format(patch_span.context.trace_id, "032x")
        assert patch_log["span_id"] == format(patch_span.context.span_id, "016x")

        # Check metrics delivery and bounded labels
        metric_data = metric_reader.get_metrics_data()
        scope_metrics = metric_data.resource_metrics[0].scope_metrics[0].metrics
        metric_names = {m.name for m in scope_metrics}
        assert "tasklane_http_requests" in metric_names
        assert "tasklane_http_duration" in metric_names

        for metric in scope_metrics:
            for point in metric.data.data_points:
                point_attrs = dict(point.attributes)
                assert set(point_attrs.keys()).issubset({"http.request.method", "http.route", "http.response.status_code"})
                assert "task_id" not in point_attrs
                assert sensitive_title not in str(point_attrs)


def test_telemetry_validation_errors_and_404_responses(monkeypatch):
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    app = _create_test_app()

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])

    enable_telemetry(app, tracer_provider=tracer_provider, meter_provider=meter_provider)
    client = TestClient(app)

    # 422 Unprocessable Entity
    resp_422 = client.post("/api/tasks", json={"title": "foo", "priority": "invalid-priority"})
    assert resp_422.status_code == 422

    # 404 Not Found
    resp_404 = client.get("/api/tasks/8888")
    assert resp_404.status_code == 404

    # 404 on unmapped route
    resp_other = client.get("/non-existent-endpoint")
    assert resp_other.status_code == 404

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 3

    # 422 span
    assert spans[0].attributes["http.response.status_code"] == 422
    assert spans[0].status.status_code == StatusCode.UNSET  # 4xx is not server error
    assert len(spans[0].events) == 0

    # 404 span on templated route
    assert spans[1].attributes["http.response.status_code"] == 404
    assert spans[1].attributes["http.route"] == "/api/tasks/{task_id}"
    assert spans[1].status.status_code == StatusCode.UNSET
    assert len(spans[1].events) == 0

    # 404 on unknown route maps to bounded "other"
    assert spans[2].attributes["http.response.status_code"] == 404
    assert spans[2].attributes["http.route"] == "other"
    assert spans[2].status.status_code == StatusCode.UNSET


# ==============================================================================
# 3. HIGH 1: Exception redaction & status description verification
# ==============================================================================


def test_telemetry_unhandled_500_redacts_sensitive_exception_marker(monkeypatch, caplog):
    """Verify that unhandled exceptions set StatusCode.ERROR with NO exception message in description, events, attributes, or logs."""
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    app = _create_test_app()

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])

    with caplog.at_level(logging.INFO, logger="tasklane.telemetry"):
        enable_telemetry(app, tracer_provider=tracer_provider, meter_provider=meter_provider)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/fail-unhandled")
        assert resp.status_code == 500

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    fail_span = spans[0]

    secret_marker = "SYNTHETIC_UNHANDLED_EXCEPTION_SECRET_TOKEN_99999"

    # Status must be StatusCode.ERROR, but description MUST be None (no exception message)
    assert fail_span.status.status_code == StatusCode.ERROR
    assert fail_span.status.description is None

    # Events must be empty: record_exception=False ensures no exception event is attached
    assert len(fail_span.events) == 0

    # Attributes must contain safe metadata only
    assert fail_span.attributes["http.request.method"] == "GET"
    assert fail_span.attributes["http.response.status_code"] == 500
    for key, val in fail_span.attributes.items():
        assert secret_marker not in str(key)
        assert secret_marker not in str(val)

    # Structured telemetry logs must NOT contain the secret marker or traceback
    telemetry_logs = [r.message for r in caplog.records if r.name == "tasklane.telemetry"]
    assert len(telemetry_logs) == 1
    log_data = json.loads(telemetry_logs[0])
    assert log_data["status_code"] == 500
    assert secret_marker not in telemetry_logs[0]
    assert "traceback" not in telemetry_logs[0].lower()


def test_telemetry_handled_500_sets_error_status_without_description(monkeypatch):
    """Verify that handled HTTP 500 responses set StatusCode.ERROR with no description."""
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    app = _create_test_app()

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])

    enable_telemetry(app, tracer_provider=tracer_provider, meter_provider=meter_provider)

    client = TestClient(app)
    resp = client.get("/api/fail-handled-500")
    assert resp.status_code == 500

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description is None
    assert len(span.events) == 0


def test_telemetry_preserves_real_exception_propagation(monkeypatch):
    """Verify that exception propagation to outer handlers is preserved."""
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    app = _create_test_app()

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    enable_telemetry(app, tracer_provider=tracer_provider)

    client = TestClient(app, raise_server_exceptions=True)
    with pytest.raises(RuntimeError, match="SYNTHETIC_UNHANDLED_EXCEPTION_SECRET_TOKEN_99999"):
        client.get("/api/fail-unhandled")


def test_telemetry_unreachable_collector_fails_open(monkeypatch):
    """Verify that requests succeed normally even if OTLP collector is unreachable."""
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    monkeypatch.setenv("TASKLANE_OTEL_ENDPOINT", "http://127.0.0.1:49123")
    app = _create_test_app()

    assert enable_telemetry(app) is True
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


# ==============================================================================
# 4. MEDIUM 4: Grafana datasource derived-field regex test
# ==============================================================================


def test_grafana_datasources_derived_field_regex():
    """Verify derived-field matcher regex extracts 32-character hex trace ID with and without spaces."""
    ds_path = Path(__file__).resolve().parent.parent.parent / "observability" / "grafana-datasources.yml"
    assert ds_path.exists(), f"Missing datasources file at {ds_path}"

    with open(ds_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    datasources = config.get("datasources", [])
    loki_ds = next((d for d in datasources if d.get("name") == "Loki"), None)
    assert loki_ds is not None, "Loki datasource definition missing"

    derived_fields = loki_ds.get("jsonData", {}).get("derivedFields", [])
    assert len(derived_fields) > 0, "No derivedFields configured in Loki datasource"

    trace_df = next((df for df in derived_fields if df.get("name") == "Trace ID"), None)
    assert trace_df is not None, "Trace ID derived field not found"
    assert trace_df.get("datasourceUid") == "tempo", f"Expected datasourceUid 'tempo', got {trace_df.get('datasourceUid')}"
    assert trace_df.get("url") == "$${__value.raw}", f"Expected url '$${{__value.raw}}', got {trace_df.get('url')}"

    matcher_pattern = trace_df.get("matcherRegex")
    compiled = re.compile(matcher_pattern)

    # 1. Real log format emitted by tasklane.telemetry (with space after colon)
    real_log = '{"event": "http_request", "method": "PATCH", "route": "/api/tasks/{task_id}", "status_code": 200, "duration_ms": 12.34, "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "span_id": "00f067aa0ba902b7"}'
    match1 = compiled.search(real_log)
    assert match1 is not None, "Failed to match trace_id with standard whitespace"
    assert match1.group(1) == "4bf92f3577b34da6a3ce929d0e0e4736"

    # 2. Compact JSON without space
    compact_log = '{"event":"http_request","trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"00f067aa0ba902b7"}'
    match2 = compiled.search(compact_log)
    assert match2 is not None, "Failed to match trace_id in compact JSON"
    assert match2.group(1) == "4bf92f3577b34da6a3ce929d0e0e4736"

    # 3. Negative checks: invalid hex or wrong length must not match
    assert compiled.search('{"trace_id": "short"}') is None
    assert compiled.search('{"trace_id": "4bf92f3577b34da6a3ce929d0e0e473g"}') is None  # 'g' is non-hex


# ==============================================================================
# 5. HIGH 2 & MEDIUM 3: Docker Compose isolation & localhost port binding
# ==============================================================================


def test_compose_observability_postgres_isolation_and_localhost_port():
    """Verify that resolved Compose configuration enforces local Postgres isolation and 127.0.0.1 port."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    base_compose = repo_root / "docker-compose.yml"
    obs_compose = repo_root / "docker-compose.observability.yml"
    assert base_compose.exists() and obs_compose.exists()

    env = os.environ.copy()
    synthetic_external_url = "postgresql://fake:fake@invalid.example.invalid/prod"
    env["DATABASE_URL"] = synthetic_external_url

    res = subprocess.run(
        ["docker", "compose", "-f", str(base_compose), "-f", str(obs_compose), "config"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    resolved_config = yaml.safe_load(res.stdout)
    services = resolved_config.get("services", {})

    # 1. Isolation check: App must NOT use external synthetic DATABASE_URL
    app_service = services.get("app", {})
    app_env = app_service.get("environment", {})
    app_db_url = app_env.get("DATABASE_URL")
    assert app_db_url == "postgresql://postgres:postgres@postgres:5432/tasklane", (
        f"Expected local postgres URL, got {app_db_url}"
    )
    assert "invalid.example.invalid" not in res.stdout, "External database host leaked into resolved configuration"

    # 2. Port check: App port MUST be bound exclusively to host IP 127.0.0.1
    app_ports = app_service.get("ports", [])
    assert len(app_ports) == 1, f"Expected exactly 1 published port for app, got {len(app_ports)}"
    assert app_ports[0].get("host_ip") == "127.0.0.1", f"App published port host_ip is not 127.0.0.1: {app_ports[0]}"
    assert str(app_ports[0].get("published")) == "8000"
    assert str(app_ports[0].get("target")) == "8000"

    # 3. All exposed services must be bound to 127.0.0.1
    for svc_name, svc_conf in services.items():
        for p in svc_conf.get("ports", []):
            assert p.get("host_ip") == "127.0.0.1", (
                f"Service {svc_name} port {p} is not restricted to host_ip 127.0.0.1"
            )
