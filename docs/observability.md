# TaskLane — Optional Local OpenTelemetry Demo (Issue #2)

**Status:** The local OpenTelemetry demo has passed end-to-end CI verification in [run 40047097348](https://github.com/LE0ST/tasklane/actions/runs/40047097348), including correlated Prometheus/Loki/Tempo evidence, Grafana datasource provisioning and a task-title log-redaction assertion. This feature remains **local-only**, is not enabled in Render and awaits independent Astra/Sol review.

## Design
`backend/telemetry.py` is a fail-open, opt-in FastAPI HTTP middleware that records:
- `tasklane_http_requests` counter and `tasklane_http_duration` histogram, with bounded method, templated route and status labels.
- OTLP HTTP spans, structured JSON request logs and the same 32-hex `trace_id`.
- Request metadata only: no task content, URL paths or query strings, authorization headers, or secrets.

Instrumentation activates only when `TASKLANE_OTEL_ENABLED=true`, the optional SDK exists and `TASKLANE_OTEL_ENDPOINT` is set. The default Render image includes `telemetry.py` but **does not install the optional SDK**; ordinary `docker compose up --build -d` is unchanged.

## Local setup (requires Docker Engine)
The optional Dockerfile derives from the base `tasklane:hw3` image. Build that first:

```bash
docker build -t tasklane:hw3 .
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.observability.yml ps
```

The Grafana UI is available at <http://localhost:3000>. This is a disposable localhost-only demo and anonymous viewing is enabled **only on localhost**. Database data is local PostgreSQL from the standard Compose stack, not Neon.

Trigger local requests:

```bash
created="$(curl -fsS -X POST http://localhost:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"otel-local-demo","priority":"low"}')"
task_id="$(printf '%s' "$created" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
curl -fsS -X PATCH "http://localhost:8000/api/tasks/$task_id" \
  -H 'Content-Type: application/json' \
  -d '{"status":"todo"}'
```

In Grafana Explore, choose the provisioned Prometheus, Loki and Tempo data sources. Search Prometheus for `tasklane_http_requests_total` (the counter's exported Prometheus name) and `tasklane_http_duration_milliseconds_bucket` (depending on Collector translation). Search Loki by `service_name="tasklane-api"` and inspect JSON log bodies for `trace_id`. Paste a trace ID into Tempo Explore to find its span. The underlying Prometheus request metric, Loki request log and Tempo trace were verified by CI; confirm histogram naming, dashboard navigation, and any derived-field links in the running UI before claiming those manual screenshots as evidence.

Stop and remove local demo:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml down
```

## Verified CI evidence
- [Run 40046821841](https://github.com/LE0ST/tasklane/actions/runs/40046821841): first successful four-job CI including correlated metrics/logs/traces.
- [Run 40047097348](https://github.com/LE0ST/tasklane/actions/runs/40047097348): all four jobs passed with added Grafana datasource and request-log redaction checks.
- CI logs state: `PASS: Task create/patch, request metric, correlated Loki JSON log and Tempo trace.` and `PASS: Grafana provisioned Prometheus/Loki/Tempo datasources and no task title appears in request log.`
- The test performed real local POST and PATCH operations, queried Prometheus and Loki HTTP APIs, retrieved the resulting trace through Tempo, and redacted the trace ID from the public CI output.
- To reproduce on a local Docker host, run `python3 observability/smoke.py` after all services start. It uses only Python standard-library modules.

## Limitations / QA checklist
- The verified CI evidence does **not** yet include manual screenshots from Grafana Explore.
- Correlation was confirmed for a successful PATCH. Concurrency and error-path correlation still need independent QA review.
- Collector exports and ingestion succeeded in CI for this single local journey. Future integration changes should preserve the automated smoke gate.
- CI confirmed the Grafana API lists provisioned Prometheus, Loki and Tempo datasources; manual Grafana Explore screenshots and clicking derived Tempo links remain unverified.
- No Render or Neon changes are authorized for Issue #2.
- Frontend, backend/PostgreSQL, Docker/Playwright and observability jobs passed in run 40047097348. Recheck the final HEAD commit before merge.
