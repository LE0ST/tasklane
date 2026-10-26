# TaskLane — Optional Local OpenTelemetry Observability Stack (Issue #2)

**Status (October 25, 2026):** The opt-in, local-only observability stack was merged through [PR #7](https://github.com/LE0ST/tasklane/pull/7) as [`cc9cce7`](https://github.com/LE0ST/tasklane/commit/cc9cce7fb27ad3b769e8d4fcf3164f597d4a7fb8). Security/SRE review findings were addressed before the human-authorized merge. This module remains disabled by default on production Render, while the additional user-impact alert was subsequently merged in PR #8. For latest passing combined evidence see [CI 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032).

---

## 1. Architectural Overview & Design Principles

TaskLane implements an opt-in, fail-open OpenTelemetry instrumentation architecture:
- **Zero Production Overhead:** The default production Docker image copies `telemetry.py` but does not install or enable the OpenTelemetry SDK. If `TASKLANE_OTEL_ENABLED` is omitted or false, `enable_telemetry` immediately returns `False` without mounting any middleware.
- **Safe Metadata Only:** Telemetry records only bounded HTTP metadata (templated routes `/api/tasks`, `/api/tasks/{task_id}`, `/health`, `/api/health`, or `other`, standard HTTP methods, and status codes). Task titles, descriptions, query parameters, authorization headers, and secrets are never exported.
- **Method Normalization (LOW):** Request methods outside `{"GET", "POST", "PATCH", "DELETE", "OPTIONS"}` are normalized to `"OTHER"`. Spans are named `HTTP <SAFE_METHOD>` (e.g. `HTTP GET`, `HTTP POST`, `HTTP OTHER`), preventing untrusted client strings from polluting span names.
- **Genuinely Fail-Open Initialization (MEDIUM 2):** Provider and exporter construction is protected against configuration/runtime exceptions (e.g. incompatible `OTEL_BSP_MAX_QUEUE_SIZE` / `OTEL_BSP_MAX_EXPORT_BATCH_SIZE`). If initialization fails, any added handlers or providers are cleanly discarded, a fixed non-sensitive warning is logged (with zero dynamic exception strings, secrets, or configuration interpolation), and the application starts normally with telemetry disabled.
- **Exception Redaction (HIGH 1):** `tracer.start_as_current_span()` explicitly specifies `record_exception=False` and `set_status_on_exception=False`. Unhandled failures and 5xx responses set `StatusCode.ERROR` with description `None`, completely preventing raw exception text, SQL queries, or stack traces from entering span events, span attributes, or structured logs. Real exception propagation to FastAPI handlers is preserved.
- **Fail-Open Resilience:** If the OTLP Collector is unreachable or unconfigured, the application continues to process HTTP traffic without latency or crashes.

```mermaid
flowchart LR
    Client["Browser / curl<br/>127.0.0.1:8000"] -->|HTTP| App["TaskLane App<br/>(FastAPI + OTel SDK 1.37.0)"]
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

### 2.1 Enforced Local Database Isolation & Fixed Exec-Form Healthcheck (HIGH 2 & MEDIUM 1)
- In `docker-compose.observability.yml`, the application's `DATABASE_URL` is overridden **unconditionally** with the local PostgreSQL service URL:
  ```yaml
  DATABASE_URL: postgresql://postgres:postgres@postgres:5432/tasklane
  ```
- The PostgreSQL service healthcheck is pinned to literal **exec-form** arguments:
  ```yaml
  healthcheck:
    test: ["CMD", "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "tasklane"]
  ```
- Overriding the base CMD-SHELL healthcheck prevents host shell interpolation of `POSTGRES_USER` or `POSTGRES_DB` and protects against shell injection or external connection redirection.
- Pinned credentials (`postgres:postgres`) and isolated storage ensure zero risk of accidental remote writes.

### 2.2 Dedicated Compose Project Identity & Noncolliding Names (MEDIUM 6)
- `docker-compose.observability.yml` defines `name: tasklane-observability` and non-colliding container names:
  - `container_name: tasklane-obs-app`
  - `container_name: tasklane-obs-postgres`
- This ensures the observability stack runs side-by-side with any existing standard local TaskLane installation (`tasklane-app`, `tasklane-postgres`) without container name collisions.
- Running `docker compose down -v` on the observability stack purges only `tasklane-observability_postgres_data`, safely preserving the developer's primary `tasklane_postgres_data` volume.

### 2.3 Localhost Port Binding via Compose `!override` (MEDIUM 3)
- Base `docker-compose.yml` publishes application port `8000:8000` across all interfaces for standard development.
- `docker-compose.observability.yml` employs the Docker Compose **`!override`** attribute directive to replace inherited port mappings with localhost-only exposure:
  ```yaml
  ports: !override
    - "127.0.0.1:8000:8000"
  ```
- All companion services are bound strictly to `127.0.0.1`:
  - Web Application: `127.0.0.1:8000`
  - PostgreSQL: `127.0.0.1:5432`
  - Grafana: `127.0.0.1:3000`
  - Prometheus: `127.0.0.1:9090`
  - Loki: `127.0.0.1:3100`
  - Tempo: `127.0.0.1:3200`
- **Minimum Docker Compose Version:** Requires **Docker Compose 2.24.4 or later** to support the `!override` list replacement syntax.

### 2.4 Grafana Derived-Field Log-to-Trace Navigation (MEDIUM 4)
- `observability/grafana-datasources.yml` provisions Loki with a whitespace-tolerant derived-field regex:
  ```yaml
  derivedFields:
    - name: Trace ID
      matcherRegex: '"trace_id"\s*:\s*"([0-9a-f]{32})"'
      datasourceUid: tempo
      url: '$${__value.raw}'
  ```
- Accurately matches standard Python JSON log output (`"trace_id": "<hex32>"`) and compact JSON (`"trace_id":"<hex32>"`), allowing seamless one-click jumping from Loki request logs directly to the correlated Tempo trace.

### 2.5 SDK Version Alignment (LOW 5)
- OpenTelemetry packages are locked to exact version **1.37.0** across all environments:
  - Development lockfile (`backend/uv.lock`): `opentelemetry-sdk==1.37.0`, `opentelemetry-exporter-otlp-proto-http==1.37.0`.
  - Docker derivative image (`Dockerfile.observability`): `opentelemetry-sdk==1.37.0`, `opentelemetry-exporter-otlp-proto-http==1.37.0`.

### 2.6 Resource Limits & Ephemeral Storage
To prevent noisy-neighbor resource exhaustion on development workstations and CI runners, all services are strictly constrained:
- `tasklane-obs-app`: `mem_limit: 384m`
- `otel-collector`: `mem_limit: 320m`
- `prometheus`: `mem_limit: 384m`, `--storage.tsdb.retention.time=24h`
- `loki`: `mem_limit: 512m`
- `tempo`: `mem_limit: 384m`
- `grafana`: `mem_limit: 512m`
- **Total Memory Footprint:** Bounded under ~2.5 GB.
- **Storage Policy:** All telemetry backends utilize ephemeral local storage. Data is cleanly purged on `docker compose down -v`.

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

# Create task (POST 201)
created="$(curl -fsS -X POST http://127.0.0.1:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"otel-demo-task","priority":"low"}')"

task_id="$(printf '%s' "$created" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"

# Update task status (PATCH 200)
curl -fsS -X PATCH "http://127.0.0.1:8000/api/tasks/$task_id" \
  -H 'Content-Type: application/json' \
  -d '{"status":"todo"}'

# Trigger client failure path (GET 404)
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
4. Prometheus metrics delivery for both success (`PATCH`) and failure (`404`).
5. Provisioned Grafana datasources (`Prometheus`, `Loki`, `Tempo`).
6. Correlated Loki JSON logs containing the exact 32-hex `trace_id` for both 200 and 404 operations, asserting no task title leakage.
7. Grafana derived-field regex extraction against real Loki log lines.
8. Tempo trace availability for both 200 and 404 `trace_id`s.

### 3.5 Safe Teardown
```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml down -v
```

---

## 4. Automated Test Coverage & Verification Evidence

### 4.1 Test Counts
- **76 backend tests** passed at the merged-main baseline `9968999` in [GitHub Actions run 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032). This current total combines API, incident-alert validation, telemetry, and PostgreSQL integration tests; it supersedes the historical 38-test count from the initial Issue #2 implementation.
- **54 additional on-call responder tests** passed in [merged PR #9](https://github.com/LE0ST/tasklane/pull/9), [PR CI 40272231320](https://github.com/LE0ST/tasklane/actions/runs/40272231320), and its [merged-main CI 40273628271](https://github.com/LE0ST/tasklane/actions/runs/40273628271). They verify the local-only diagnostic responder, not live AI-authored repair.

### 4.2 Telemetry Test Breakdown (`backend/tests/test_telemetry.py`)
The telemetry and isolation tests verify:
- **Default Disabled State:** `enable_telemetry` returns `False` when flag is omitted; HTTP requests execute normally with zero telemetry logging.
- **Fail-Open Behavior:** Missing SDK packages (`ImportError`) and unreachable OTLP endpoints fail open without crashing or blocking HTTP traffic.
- **Incompatible Configuration Fail-Open:** Tests incompatible batch processor settings (`OTEL_BSP_MAX_QUEUE_SIZE=1`, `OTEL_BSP_MAX_EXPORT_BATCH_SIZE=2`), asserting that `enable_telemetry` returns `False`, no middleware is installed, a sanitized warning is logged without leaking raw exception details or configuration values, and `/health` serves traffic normally.
- **Initialization Warning Sanitization Regression:** Injects an initialization failure containing a synthetic secret marker and URL credentials into TracerProvider construction, verifying `enable_telemetry` returns `False`, no middleware is installed, a fixed non-sensitive warning is logged without dynamic exception interpolation or secret leakage, and the application serves traffic normally.
- **Method Normalization:** Synthetic nonstandard method (`PROPFIND`) normalizes span name to `HTTP OTHER`, attribute `http.request.method` to `"OTHER"`, metric label to `"OTHER"`, and log entry to `"OTHER"`.
- **Schema Validation & Not Found Handling (422 & 404):** Tests a standalone FastAPI test application using TaskLane's real Pydantic schemas (triggering 422 Unprocessable Entity) and representative 404 routes, asserting status classification (`StatusCode.UNSET`), empty events, and cross-signal trace correlation.
- **In-Memory Telemetry Assertions:**
  - POST and PATCH spans are created as `SpanKind.SERVER`.
  - Span attributes contain safe metadata only (`http.request.method`, `http.route`, `http.response.status_code`).
  - Sensitive task titles and descriptions never appear in span attributes, events, or structured logs.
  - Metrics counter and duration histogram are delivered with bounded labels (excluding task IDs).
- **Error Path & Redaction Verification:**
  - Unhandled 500 exceptions and handled 500 responses set `StatusCode.ERROR` with description `None`.
  - Synthetic secret markers are confirmed absent from span attributes, span events, status descriptions, and logs.
  - Real exception propagation to outer handlers is preserved.
- **Regex & Datasource Integrity:** PyYAML validation confirms Loki `derivedFields` uses `datasourceUid: tempo` and matches both standard and compact JSON trace IDs.
- **Hostile Variable Compose Isolation Regression Test:** Verifies via `docker compose config` with synthetic hostile `DATABASE_URL`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` that the app strictly targets local PostgreSQL, uses the fixed exec-form healthcheck, binds only to `127.0.0.1:8000`, and contains zero hostile strings.

### 4.3 CI Pipeline Integration
- Run in GitHub Actions with 4 parallel/sequential verification jobs:
  1. `Frontend Build & Asset Verification`
  2. `Backend Tests & PostgreSQL Integration` (76 tests at baseline `9968999`)
  3. `Docker Build, Smoke Tests & Playwright E2E`
  4. `OpenTelemetry Local Stack & Correlation` (`observability-smoke`)
