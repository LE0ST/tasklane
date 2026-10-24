# TaskLane — Optional Local OpenTelemetry Observability Stack (Issue #2)

**Status:** Updated to resolve SRE Audit findings from **GPT-6 Astra Medium**. The local OpenTelemetry demo includes fail-open request telemetry, sensitive exception redaction, local-only database isolation, localhost port bindings, and Grafana log-to-trace navigation. This stack is **local-only**, strictly disabled on Render production, and requires human maintainer authorization before merging.

---

## 1. Architectural Overview & Design Principles

TaskLane implements an opt-in, fail-open OpenTelemetry instrumentation architecture:
- **Zero Production Overhead:** The default production Docker image copies `telemetry.py` but does not install or enable the OpenTelemetry SDK. If `TASKLANE_OTEL_ENABLED` is omitted or false, `enable_telemetry` immediately returns `False` without mounting any middleware.
- **Safe Metadata Only:** Telemetry records only bounded HTTP metadata (templated routes `/api/tasks`, `/api/tasks/{task_id}`, `/health`, `/api/health`, or `other`, standard HTTP methods, and status codes). Task titles, descriptions, query parameters, authorization headers, and secrets are never exported.
- **Exception Redaction (HIGH 1):** `tracer.start_as_current_span()` explicitly specifies `record_exception=False` and `set_status_on_exception=False`. Unhandled failures and 5xx responses set `StatusCode.ERROR` with an empty description (`None`), completely preventing raw exception text, SQL queries, or stack traces from entering span events, span attributes, or structured logs.
- **Fail-Open Reliability:** If the OTLP Collector is unreachable or unconfigured, the application continues to process HTTP traffic without latency or crashes.

```mermaid
flowchart LR
    Client["Browser / curl<br/>127.0.0.1:8000"] -->|HTTP| App["TaskLane App<br/>(FastAPI + OTel SDK)"]
    App -->|Local DB| PG["PostgreSQL 16<br/>127.0.0.1:5432"]
    App -->|OTLP HTTP :4318| OTelCol["OTel Collector Contrib<br/>(Internal :4318)"]
    OTelCol -->|Prometheus Exporter| Prom["Prometheus<br/>127.0.0.1:9090"]
    OTelCol -->|Loki OTLP Exporter| Loki["Loki<br/>127.0.0.1:3100"]
    OTelCol -->|OTLP gRPC Exporter| Tempo["Tempo<br/>127.0.0.1:3200"]
    Grafana["Grafana<br/>127.0.0.1:3000"] -->|Query| Prom
    Grafana -->|Query & Derived Fields| Loki
    Grafana -->|Trace ID Navigation| Tempo
```

---

## 2. Security, Isolation & Operational Controls

### 2.1 Enforced Local Database Isolation (HIGH 2)
- In `docker-compose.observability.yml`, the application's `DATABASE_URL` is overridden **unconditionally** with the local PostgreSQL service URL:
  ```yaml
  DATABASE_URL: postgresql://postgres:postgres@postgres:5432/tasklane
  ```
- Even if a developer has an external `DATABASE_URL` (e.g. pointing to Render or Neon) exported in their host shell environment, the resolved Compose configuration strictly targets the local `postgres` container.
- Disposable local credentials (`postgres:postgres`) and dedicated volume storage (`postgres_data`) ensure zero risk of accidental remote writes during observability drills.

### 2.2 Localhost Port Binding via Compose `!override` (MEDIUM 3)
- Base `docker-compose.yml` publishes application port `8000:8000` across all interfaces for standard development.
- `docker-compose.observability.yml` employs the Docker Compose **`!override`** attribute directive to replace inherited port mappings with localhost-only exposure:
  ```yaml
  ports: !override
    - "127.0.0.1:8000:8000"
  ```
- All observability infrastructure ports are bound exclusively to `127.0.0.1`:
  - Web Application: `127.0.0.1:8000`
  - PostgreSQL: `127.0.0.1:5432`
  - Grafana: `127.0.0.1:3000`
  - Prometheus: `127.0.0.1:9090`
  - Loki: `127.0.0.1:3100`
  - Tempo: `127.0.0.1:3200`
- **Minimum Docker Compose Version:** Requires **Docker Compose 2.24.4 or later** to support the `!override` list replacement syntax.

### 2.3 Grafana Derived-Field Log-to-Trace Navigation (MEDIUM 4)
- `observability/grafana-datasources.yml` provisions Loki with a whitespace-tolerant derived-field regex:
  ```yaml
  derivedFields:
    - name: Trace ID
      matcherRegex: '"trace_id"\s*:\s*"([0-9a-f]{32})"'
      datasourceUid: tempo
      url: '$${__value.raw}'
  ```
- Accurately matches standard Python JSON log output (`"trace_id": "<hex32>"`) and compact JSON (`"trace_id":"<hex32>"`), allowing seamless one-click jumping from Loki request logs directly to the correlated Tempo trace.

### 2.4 Resource Limits & Ephemeral Storage
To prevent noisy-neighbor resource exhaustion on development workstations and CI runners, all services are strictly constrained:
- `tasklane-app`: `mem_limit: 384m`
- `otel-collector`: `mem_limit: 320m`
- `prometheus`: `mem_limit: 384m`, `--storage.tsdb.retention.time=24h`
- `loki`: `mem_limit: 512m`
- `tempo`: `mem_limit: 384m`
- `grafana`: `mem_limit: 512m`
- **Total Memory Footprint:** Bounded under ~2.5 GB.
- **Storage Policy:** All telemetry backends utilize ephemeral local storage intended for active debugging, regression verification, and incident drills. Data is cleanly purged on `docker compose down -v`.

---

## 3. Local Setup & Verification Instructions

### 3.1 Prerequisites
- Docker Engine & Docker Compose (v2.24.4+).
- Python 3.10+ (for running smoke verification scripts).

### 3.2 Starting the Observability Stack
Build the base application image first, then launch with the observability override:

```bash
# 1. Build base image
docker build -t tasklane:hw3 .

# 2. Start stack in background with observability override
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d

# 3. Verify services are healthy and running
docker compose -f docker-compose.yml -f docker-compose.observability.yml ps
```

### 3.3 Generating Telemetry
Send sample traffic to the local API:

```bash
# Health check
curl -fsS http://127.0.0.1:8000/health

# Create task (POST)
created="$(curl -fsS -X POST http://127.0.0.1:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"otel-demo-task","priority":"low"}')"

task_id="$(printf '%s' "$created" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"

# Update task status (PATCH)
curl -fsS -X PATCH "http://127.0.0.1:8000/api/tasks/$task_id" \
  -H 'Content-Type: application/json' \
  -d '{"status":"todo"}'

# Trigger 404 client error
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/tasks/999999
```

### 3.4 Automated Smoke Verification
Execute the automated correlation test:

```bash
python3 observability/smoke.py
```

The script verifies:
1. Application health endpoint.
2. Successful task creation and PATCH status updates.
3. Client failure paths (404 Not Found).
4. Prometheus metrics delivery (`tasklane_http_requests_total`).
5. Provisioned Grafana datasources (`Prometheus`, `Loki`, `Tempo`).
6. Correlated Loki JSON logs containing the exact 32-hex `trace_id` and asserting no task title leakage.
7. Grafana derived-field regex extraction against real Loki log lines.
8. Tempo trace availability matching the correlated `trace_id`.

### 3.5 Teardown
```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml down -v
```

---

## 4. Automated Test Coverage & Verification Evidence

### 4.1 Unit & Isolation Test Suite (`backend/tests/test_telemetry.py`)
15 automated tests verify:
- **Default Disabled State:** `enable_telemetry` returns `False` when flag is omitted; HTTP requests execute normally with zero telemetry logging.
- **Fail-Open Behavior:** Missing SDK packages (`ImportError`) and unreachable OTLP endpoints fail open without crashing or blocking HTTP traffic.
- **Configuration Validation:** Empty, whitespace, or non-HTTP endpoints log warnings and reject initialization.
- **In-Memory Telemetry Assertions:**
  - POST and PATCH spans are created as `SpanKind.SERVER`.
  - Span attributes contain safe metadata only (`http.request.method`, `http.route`, `http.response.status_code`).
  - Sensitive task titles and descriptions never appear in span attributes, events, or structured logs.
  - Metrics counter and duration histogram are delivered with bounded labels (excluding task IDs).
- **Error Path & Redaction Verification:**
  - 422 validation errors and 404 not found spans retain `StatusCode.UNSET` (non-server errors).
  - Unhandled 500 exceptions and handled 500 responses set `StatusCode.ERROR` with description `None`.
  - Synthetic secret markers are confirmed absent from span attributes, span events, status descriptions, and logs.
  - Real exception propagation to outer handlers is preserved.
- **Regex & Datasource Integrity:** PyYAML validation confirms Loki `derivedFields` uses `datasourceUid: tempo` and matches both standard and compact JSON trace IDs.
- **Compose Isolation Regression Test:** Verifies via `docker compose config` with a synthetic external `DATABASE_URL` that the app strictly targets local PostgreSQL and binds only to `127.0.0.1:8000`.

### 4.2 CI Pipeline Integration
- Run in GitHub Actions with 4 parallel/sequential verification jobs:
  1. `Frontend Build & Asset Verification`
  2. `Backend Tests & PostgreSQL Integration`
  3. `Docker Build, Smoke Tests & Playwright E2E`
  4. `OpenTelemetry Local Stack & Correlation` (`observability-smoke`)

---

## 5. Verified Functionality vs. Remaining Limitations

| Area | Verified Functionality | Remaining Limitations / Future Work |
|:---|:---|:---|
| **Trace Ingestion** | OTLP traces ingested by Tempo; queryable via Tempo API with 32-hex `trace_id`. | Manual UI screenshots from Grafana Explore remain an optional manual evidence task. |
| **Log Correlation** | Loki receives structured JSON logs; derived-field regex extracts `trace_id`. | Interactive point-and-click UI verification in browser is unautomated. |
| **Metric Export** | Prometheus scrapes OTel Collector; counter and duration histogram queryable. | Alert rules and Grafana alert manager configuration belong to Issue #3. |
| **Database Isolation** | Compose override unconditionally targets local PostgreSQL; verified by regression test. | None; strictly isolated. |
| **Port Security** | `!override` restricts app to `127.0.0.1:8000`; all backends bound to `127.0.0.1`. | Requires Docker Compose 2.24.4+. |
| **Production Impact** | Zero; SDK uninstalled in base production image, disabled by default. | Must not be enabled on Render. |
