# Issue #3 — User-Impact 5xx Alert and Local Incident Drill

**Status:** Implementation awaiting CI evidence and independent QA. All changes are limited to the disposable, local observability Compose stack. The live Render and Neon services must not be used for this drill.

## What we detect
The versioned Grafana-managed `TaskLane5xxRate` rule reads Prometheus `tasklane_http_requests_total`, restricted to TaskLane task endpoints and HTTP 5xx responses. It evaluates every 10s and alerts when at least one task-request 5xx was seen in a sliding 2-minute window. It **does not** alert on CPU-only metrics, expected 4xx responses, static files, or health-check requests.

The query intentionally falls back to zero only when task-request time series exist. An unavailable telemetry source remains **NoData** or **Error**, not a false healthy zero. A separate missing-telemetry alert is a possible later enhancement.

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

## Evidence checklist
- [ ] CI confirms the actual 503 and healthy post-drill API
- [ ] CI confirms task was not created during injected failure
- [ ] Prometheus has TaskLane task 5xx counter data
- [ ] Loki POST 503 log contains matching safe route and nonzero trace ID
- [ ] Tempo returns corresponding server trace
- [ ] Grafana webhook contact point delivers `firing`
- [ ] After the 2-minute rolling window, Grafana delivers `resolved`
- [ ] Frontend/backend/PostgreSQL/Playwright regression checks remain passing
- [ ] Optional Grafana UI screenshots captured locally

## Security and limitations
- No production services or database are part of this demonstration.
- The rule detects request-level impact in a rolling 2-minute window; it is not a complete SLO with paging or durable incident history.
- A missing source is not silently treated as healthy. Operator should inspect Grafana NoData/Error separately.
- `alert-sink` is memory-only and intentionally has no credentials, storage, code execution, remediation commands, or privileged connectivity.
- CPU/RAM use increases while six telemetry containers and the receiver run; stop the optional stack when done.
