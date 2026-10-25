"""Issue #3 local drill and alert provisioning guardrail tests."""
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from incident_drill import install_local_drill

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
    assert "tasklane_http_requests_total" in expr
    assert "http_response_status_code" in expr and '"5.."' in expr
    assert "/api/tasks" in expr and "[2m]" in expr
    assert "cpu" not in expr.lower() and "health" not in expr.lower()
    assert rule["noDataState"] == "NoData" and rule["execErrState"] == "Error"
    receivers = config["contactPoints"][0]["receivers"]
    assert receivers[0]["type"] == "webhook"
    assert receivers[0]["settings"]["url"] == "http://alert-sink:8080/alerts"
    assert config["policies"][0]["receiver"] == config["contactPoints"][0]["name"]
