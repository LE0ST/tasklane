# TaskLane — Optional Local OpenTelemetry Demo (Issue #2)

**Status:** Implementation draft; end-to-end Docker evidence is not yet verified. This is not enabled in Render.

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
curl -fsS -X POST http://localhost:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"otel-local-demo","priority":"low"}'
# Capture the task ID from the response and use it below.
curl -fsS -X PATCH http://localhost:8000/api/tasks/1 \
  -H 'Content-Type: application/json' \
  -d '{"status":"todo"}'
```

In Grafana Explore, choose the provisioned Prometheus, Loki and Tempo data sources. Search Prometheus for `tasklane_http_requests_total` (the counter's exported Prometheus name) and `tasklane_http_duration_milliseconds_bucket` (depending on Collector translation). Search Loki by `service_name="tasklane-api"` and inspect JSON log bodies for `trace_id`. Paste a trace ID into Tempo Explore to find its span. **Do not claim these queries are successful until verified in the running local stack**; consult actual scraped metric names if a name differs.

Stop and remove local demo:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml down -v
```

## Limitations / QA checklist
- End-to-end ingestion and Grafana correlation require a real local Docker run and captured evidence; not demonstrated by source code alone.
- Cross-signal trace correlation needs careful verification for concurrent requests and error paths.
- The Collector must be checked for successful exports into Loki's OTLP endpoint and Tempo's HTTP receiver, as well as Prometheus scraping.
- Verify that Grafana data sources resolve in the running Compose network.
- No Render or Neon changes are authorized for Issue #2.
- Run existing FastAPI, PostgreSQL integration, frontend build and Playwright tests and document actual outcomes.
