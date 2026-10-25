# Issue #3 — User-Impact 5xx Alert and Local Incident Drill

**Status:** End-to-end local firing and recovery were verified in [GitHub Actions run 40221894536](https://github.com/LE0ST/tasklane/actions/runs/40221894536). Independent Astra/Sol review and maintainer merge authorization remain pending. All changes are limited to the disposable, local observability Compose stack. The live Render and Neon services must not be used for this drill.

## What we detect
The versioned Grafana-managed `TaskLane5xxRate` rule evaluates the dedicated aggregate counter `sum(increase(tasklane_task_5xx_errors_total[2m]))`, which counts HTTP 5xx responses strictly across TaskLane task endpoints (`/api/tasks`, `/api/tasks/{task_id}`). It evaluates every 10s and alerts when at least one task-request 5xx was seen in a sliding 2-minute window. It **does not** alert on CPU-only metrics, expected 4xx responses, static files, or health-check requests.

The aggregate counter is initialized at zero during telemetry startup, ensuring even the first POST/500, PATCH/500, or local 503 is detected reliably without artificial per-status baseline seeding. Note that first-error detection requires Prometheus to observe the initialized counter baseline (requiring Prometheus to observe the zero baseline across scrape intervals before `increase()` computes a positive delta). If the application or telemetry exporter is unavailable, the Prometheus query returns No Data, preserving Grafana's **NoData** or **Error** behavior rather than masking missing telemetry as healthy. Note: Detection of missing telemetry is not instantaneous; cached metrics and scrape intervals may delay detection of telemetry loss, potentially for several minutes until Prometheus scrape timeouts and staleness deltas elapse. A separate missing-telemetry alert is a possible later enhancement.

Grafana provisions the rule, webhook contact point `tasklane-local-drill`, and notification policy from `observability/alerting/tasklane.yml`. Notifications are delivered exclusively within the local Compose network to `http://alert-sink:8080/alerts`. The Python standard-library receiver stores only allowlisted `TaskLane5xxRate` statuses (`firing` / `resolved`) in RAM; it never logs or executes untrusted webhook content. The receiver is a simple local evidence sink, **not** the on-call agent planned for Issue #4.

## Safe fault injection
`backend/incident_drill.py` registers a minimal middleware **only** if all the following match:
- `TASKLANE_INCIDENT_DRILL_ENABLED=local-only`
- `TASKLANE_OTEL_ENABLED=true`
- `TASKLANE_OTEL_ENDPOINT=http://otel-collector:4318`
- `DATABASE_URL=postgresql://postgres:postgres@postgres:5432/tasklane`

All conditions are explicitly set only by the optional local Compose override. A specially labeled `POST /api/tasks` request with header `X-TaskLane-Drill: inject-503` receives **503 Service Unavailable** without database mutation. The existing OpenTelemetry middleware wraps this response and records its metrics, log, trace ID and error status. Ordinary task requests remain unchanged. Production defaults do not enable telemetry or this injection.

**Never enable these local-only environment variables on Render. Never point this stack at Neon.**

## Reproduce (Docker Compose >= 2.24.4, Python 3)
Use an isolated Docker host or stop an ordinary local TaskLane stack first: both publish localhost ports 8000 and 5432. The optional stack uses its own Compose project and PostgreSQL volume.

```bash
docker build -t tasklane:hw3 .
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
python3 observability/smoke.py
python3 observability/incident_smoke.py
```

The second test waits for the actual Grafana webhook status **firing**, followed by **resolved** after the 2-minute metric window ages out. It verifies a real 503 for task creation, absence of the failed task in PostgreSQL, a 5xx Prometheus metric, a sanitized Loki request log, and the corresponding Tempo trace.

Local interfaces:
- Grafana: http://127.0.0.1:3000 (Alerting → Alert rules → TaskLane5xxRate)
- Prometheus: http://127.0.0.1:9090
- Loki: http://127.0.0.1:3100
- Tempo: http://127.0.0.1:3200
- Sanitized webhook evidence: http://127.0.0.1:8088/events

The receiver never exposes external ports. For the screenshots or evaluator review, use Grafana Explore on a local machine. **Do not claim screenshots or a firing/recovery run until they were observed** in the executable CI job.

Stop only this Compose project when finished:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml down
```

`down -v` deletes **only this demo project's** database volume, but use it only when intentionally discarding demo data. Local telemetry backends use ephemeral storage.

## Verified CI evidence
- The local drill confirms a non-mutating 503 with Prometheus/Loki/Tempo evidence, a delivered Grafana `firing` webhook, followed by a delivered terminal `resolved` webhook and zero PromQL error rate. Where supported, Grafana rule state recovery is optionally checked via the rule management API endpoint.
- Negative tests verify strict rejection of zero counter values, nonfinite/negative/unchanged metrics (NaN, +/-inf), UNSET/OK traces, wrong trace IDs/status codes, resolved-before-firing or malformed webhook histories, and NoData recovery queries.
- First-error detection is supported by the dedicated aggregate `tasklane_task_5xx_errors_total` counter initialized at zero during telemetry startup, active once Prometheus observes the zero baseline.
- **Scope:** Programmatic CI and webhook API evidence, not a manual Grafana screenshot.

## Evidence checklist
- [x] CI confirms the actual 503 and healthy post-drill API
- [x] CI confirms task was not created during injected failure
- [x] Prometheus has TaskLane task 5xx counter data
- [x] Loki POST 503 log contains matching safe route and nonzero trace ID
- [x] Tempo returns corresponding server trace
- [x] Grafana webhook contact point delivers `firing`
- [x] After the 2-minute rolling window, Grafana delivers `resolved`
- [x] Frontend/backend/PostgreSQL/Playwright regression checks remain passing
- [ ] Optional Grafana UI screenshots captured locally

## Security and limitations
- No production services or database are part of this demonstration.
- The rule detects request-level impact in a rolling 2-minute window; it is not a complete SLO with paging or durable incident history.
- A missing source is not silently treated as healthy. Operator should inspect Grafana NoData/Error separately.
- `alert-sink` is memory-only and intentionally has no credentials, storage, code execution, remediation commands, or privileged connectivity.
- CPU/RAM use increases while six telemetry containers and the receiver run; stop the optional stack when done.
