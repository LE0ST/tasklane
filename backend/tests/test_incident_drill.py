"""Issue #3 local drill and alert provisioning guardrail tests."""
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from incident_drill import install_local_drill
from telemetry import enable_telemetry

ROOT = Path(__file__).resolve().parents[2]


def _app():
    app = FastAPI()
    mutations = []

    @app.post("/api/tasks", status_code=201)
    def create_task(payload: dict):
        mutations.append(payload)
        return {"status": "backlog"}

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    return app, mutations


def _enable(monkeypatch):
    monkeypatch.setenv("TASKLANE_INCIDENT_DRILL_ENABLED", "local-only")
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    monkeypatch.setenv("TASKLANE_OTEL_ENDPOINT", "http://otel-collector:4318")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/tasklane")


def test_drill_off_by_default(monkeypatch):
    monkeypatch.delenv("TASKLANE_INCIDENT_DRILL_ENABLED", raising=False)
    app, mutations = _app()
    assert install_local_drill(app) is False
    result = TestClient(app).post("/api/tasks", headers={"x-tasklane-drill": "inject-503"}, json={"title": "safe"})
    assert result.status_code == 201 and len(mutations) == 1


def test_drill_requires_all_local_guards(monkeypatch):
    _enable(monkeypatch)
    for key, value in [
        ("TASKLANE_OTEL_ENABLED", "false"),
        ("TASKLANE_OTEL_ENDPOINT", "https://external.example.invalid"),
        ("DATABASE_URL", "postgresql://fake:fake@remote.invalid/prod"),
        ("TASKLANE_INCIDENT_DRILL_ENABLED", "true"),
    ]:
        original = __import__("os").environ[key]
        monkeypatch.setenv(key, value)
        app, _ = _app()
        assert install_local_drill(app) is False
        monkeypatch.setenv(key, original)


def test_local_drill_returns_real_503_without_db_mutation(monkeypatch):
    _enable(monkeypatch)
    app, mutations = _app()
    assert install_local_drill(app) is True
    client = TestClient(app)
    fail = client.post("/api/tasks", headers={"x-tasklane-drill": "inject-503"}, json={"title": "should_not_write"})
    assert fail.status_code == 503
    assert fail.json()["detail"] == "Controlled local service-unavailable drill"
    assert not mutations
    success = client.post("/api/tasks", json={"title": "normal"})
    assert success.status_code == 201
    assert mutations == [{"title": "normal"}]
    assert client.get("/health").status_code == 200


def test_alert_rule_and_webhook_are_versioned_and_user_facing():
    config = yaml.safe_load((ROOT / "observability/alerting/tasklane.yml").read_text(encoding="utf-8"))
    group = config["groups"][0]
    rule = group["rules"][0]
    expr = rule["data"][0]["model"]["expr"]
    assert rule["uid"] == "tasklane_5xx_rate"
    assert rule["title"] == "TaskLane5xxRate"
    assert "tasklane_task_5xx_errors_total" in expr
    assert "increase" in expr and "[2m]" in expr
    assert "cpu" not in expr.lower() and "health" not in expr.lower()
    assert rule["noDataState"] == "NoData" and rule["execErrState"] == "Error"
    receivers = config["contactPoints"][0]["receivers"]
    assert receivers[0]["type"] == "webhook"
    assert receivers[0]["settings"]["url"] == "http://alert-sink:8080/alerts"
    assert config["policies"][0]["receiver"] == config["contactPoints"][0]["name"]


# ==============================================================================
# Aggregate Task 5xx Counter Regressions (Blocker 1)
# ==============================================================================

def _setup_instrumented_app(monkeypatch):
    monkeypatch.setenv("TASKLANE_OTEL_ENABLED", "true")
    monkeypatch.setenv("TASKLANE_OTEL_ENDPOINT", "http://127.0.0.1:4318")
    app = FastAPI()

    tasks_db = {}
    counter = [1]

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    @app.get("/health-500")
    def health_500():
        raise RuntimeError("Health check failure")

    @app.get("/api/tasks")
    def list_tasks():
        return list(tasks_db.values())

    @app.post("/api/tasks", status_code=201)
    def create_task(payload: dict):
        if payload.get("trigger_500"):
            raise RuntimeError("Database connection lost during POST")
        tid = counter[0]
        counter[0] += 1
        record = {"id": tid, "title": payload.get("title", "")}
        tasks_db[tid] = record
        return record

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int):
        if task_id not in tasks_db:
            raise HTTPException(status_code=404, detail="Task not found")
        return tasks_db[task_id]

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: int, payload: dict):
        if payload.get("trigger_500"):
            raise RuntimeError("Database connection lost during PATCH")
        if task_id not in tasks_db:
            raise HTTPException(status_code=404, detail="Task not found")
        tasks_db[task_id].update(payload)
        return tasks_db[task_id]

    resource = Resource.create({"service.name": "test-incident"})
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    tracer_provider = TracerProvider(resource=resource)

    enable_telemetry(app, tracer_provider=tracer_provider, meter_provider=meter_provider)
    return app, metric_reader


def _get_aggregate_5xx_counter(metric_reader):
    data = metric_reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == "tasklane_task_5xx_errors":
                    pts = list(m.data.data_points)
                    if pts:
                        return pts[0].value, dict(pts[0].attributes)
    return None, None


def test_aggregate_5xx_counter_initialized_at_zero(monkeypatch):
    """Aggregate 5xx counter must be initialized at 0 on telemetry startup with no high-cardinality labels."""
    _, metric_reader = _setup_instrumented_app(monkeypatch)
    val, attrs = _get_aggregate_5xx_counter(metric_reader)
    assert val == 0
    assert attrs == {}

def test_aggregate_5xx_counter_detects_first_post_500(monkeypatch):
    """First POST /api/tasks 500 increments aggregate counter from 0 to 1."""
    app, metric_reader = _setup_instrumented_app(monkeypatch)
    val_before, _ = _get_aggregate_5xx_counter(metric_reader)
    assert val_before == 0

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/tasks", json={"trigger_500": True})
    assert resp.status_code == 500

    val_after, attrs = _get_aggregate_5xx_counter(metric_reader)
    assert val_after == 1
    assert attrs == {}


def test_aggregate_5xx_counter_detects_first_patch_500(monkeypatch):
    """First PATCH /api/tasks/{task_id} 500 increments aggregate counter from 0 to 1."""
    app, metric_reader = _setup_instrumented_app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    # Create task first (201)
    resp_create = client.post("/api/tasks", json={"title": "normal"})
    assert resp_create.status_code == 201

    val_before, _ = _get_aggregate_5xx_counter(metric_reader)
    assert val_before == 0

    # Inject PATCH failure (500)
    resp_patch = client.patch("/api/tasks/1", json={"trigger_500": True})
    assert resp_patch.status_code == 500

    val_after, attrs = _get_aggregate_5xx_counter(metric_reader)
    assert val_after == 1
    assert attrs == {}


def test_aggregate_5xx_counter_detects_local_post_503_drill(monkeypatch):
    """Local guarded POST 503 drill increments aggregate counter from 0 to 1."""
    _enable(monkeypatch)
    app = FastAPI()

    @app.post("/api/tasks", status_code=201)
    def create_task(payload: dict):
        return {"status": "created"}

    install_local_drill(app)

    resource = Resource.create({"service.name": "test-incident"})
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    tracer_provider = TracerProvider(resource=resource)
    enable_telemetry(app, tracer_provider=tracer_provider, meter_provider=meter_provider)

    val_before, _ = _get_aggregate_5xx_counter(metric_reader)
    assert val_before == 0

    client = TestClient(app)
    resp = client.post("/api/tasks", headers={"x-tasklane-drill": "inject-503"}, json={"title": "drill"})
    assert resp.status_code == 503

    val_after, attrs = _get_aggregate_5xx_counter(metric_reader)
    assert val_after == 1
    assert attrs == {}


def test_aggregate_5xx_counter_ignores_2xx_4xx_and_health_errors(monkeypatch):
    """Successful 2xx, client 4xx, and non-task health 500s do NOT increment task 5xx counter."""
    app, metric_reader = _setup_instrumented_app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Successful POST 201
    assert client.post("/api/tasks", json={"title": "test"}).status_code == 201
    # 2. Successful GET 200
    assert client.get("/api/tasks").status_code == 200
    # 3. Successful GET task 200
    assert client.get("/api/tasks/1").status_code == 200
    # 4. Client 404
    assert client.get("/api/tasks/9999").status_code == 404
    # 5. Health 200
    assert client.get("/health").status_code == 200
    # 6. Health 500 (non-task route)
    assert client.get("/health-500").status_code == 500

    # Aggregate task 5xx counter must remain strictly 0
    val, attrs = _get_aggregate_5xx_counter(metric_reader)
    assert val == 0
    assert attrs == {}
