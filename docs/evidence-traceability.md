# TaskLane — Project 1 Evidence and Traceability Matrix

**As-of date:** October 26, 2026 (Lima). **Merged `main` baseline:** [`7c73446`](https://github.com/LE0ST/tasklane/commit/7c7344690bf3260297301a14f434d00cb6eb123a). PR #9 has passed external SRE/security review for its restricted diagnosis-only scope and was merged with explicit maintainer authorization. Deployment and postmerge CI checks are tracked separately.

This is an auditable index, **not** a claim of grades awarded by peer reviewers. Source evidence is public issue/PR/CI history plus read-only cloud checks. External AI audit summaries are recorded as PR discussion comments, not represented as native GitHub approval reviews.

## Delivery chain: issue → PR → merged commit → CI

| Workstream | Tracker | Pull request and result | Verified evidence |
|---|---|---|---|
| Agent workflow and modular context | [Issue #1](https://github.com/LE0ST/tasklane/issues/1) — closed | [PR #6](https://github.com/LE0ST/tasklane/pull/6) — squash `f6f4c7b` | [AGENTS.md](../AGENTS.md), [teams](teams.md), [process](process.md), [architecture](architecture.md) |
| Real local OpenTelemetry | [Issue #2](https://github.com/LE0ST/tasklane/issues/2) — closed | [PR #7](https://github.com/LE0ST/tasklane/pull/7) — squash `cc9cce7` | [observability guide](observability.md), correlated metrics/logs/traces and Grafana Explore |
| Task-operation HTTP 5xx alert | [Issue #3](https://github.com/LE0ST/tasklane/issues/3) — closed | [PR #8](https://github.com/LE0ST/tasklane/pull/8) — squash `9968999` | [local 503 drill](incident-drill.md); [merged-main CI 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032), 4/4 jobs, 76 backend tests |
| On-call diagnostic receiver | [Issue #4](https://github.com/LE0ST/tasklane/issues/4) — open (partial acceptance) | [PR #9](https://github.com/LE0ST/tasklane/pull/9), merged squash `7c73446` | [PR CI 40272231320](https://github.com/LE0ST/tasklane/actions/runs/40272231320), 4/4 jobs, 76 backend + 54 responder tests; real Grafana delivery, read-only diagnosis, **mock-only repair**. [New main CI 40273628271](https://github.com/LE0ST/tasklane/actions/runs/40273628271) pending independent confirmation. |
| Final verifiability and submission | [Issue #5](https://github.com/LE0ST/tasklane/issues/5) — open | Documentation branch pending review/merge | [operations](operations-runbook.md), [submission checklist](submission-checklist.md), this matrix |

## Official rubric — evidence pointers (12 criteria, 24 available points)

The [official course project rubric](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md) requires clear English documentation and **three peer reviews**. It gives the on-call criterion 2 points only if an agent wakes on an alert **and fixes** the issue. Therefore the deterministic mock repair does not establish those 2 points.

| Criterion | Public evidence | Limitation or evidence boundary |
|---|---|---|
| Problem Description | [README overview](../README.md#project-overview--evolution), [functional spec](../_docs/specs.md) | Reviewer assigns points |
| Product Specification | [spec](../_docs/specs.md), [OpenAPI](../openapi.yaml), [Issues #1–#5](https://github.com/LE0ST/tasklane/issues) | Acceptance criteria tracked in public issues |
| Agent Team Specification | [roles](teams.md), [13-stage process](process.md), PR review handoffs | External AI review comments are not formal GitHub approvals |
| Context Management | [AGENTS.md](../AGENTS.md) links modular docs | Reviewed by course peers |
| Application | [public Render demo](https://tasklane-app.onrender.com), [backend](../backend/main.py), [frontend](../frontend/src/App.jsx) | UI-to-API-to-Neon persistence evidence from historical migration and current read-only DB verification |
| Testing | [CI workflow](../.github/workflows/ci.yml), [Actions 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032) | 76 backend + 54 responder tests in last PR #9 CI, with merged-main postmerge run pending |
| Containerization | [Dockerfile](../Dockerfile), [one-command Compose](../docker-compose.yml) | Base stack includes local PostgreSQL 16; optional observability is a separate overlay |
| Deployment | [Render public URL](https://tasklane-app.onrender.com), [operations runbook](operations-runbook.md) | Render is verified `live` at merged main `7c73446` after postmerge CI 40273628271 passed 4/4; no production write probes |
| CI/CD | [Actions workflow](../.github/workflows/ci.yml), Render `main` autoDeployTrigger=`checksPass` | Automatic production deployment; **no separate staging environment or manual production promotion** is claimed |
| Observability | [OTel guide](observability.md), [PR #7](https://github.com/LE0ST/tasklane/pull/7) | Local-only Prometheus/Loki/Tempo/Grafana, not production telemetry |
| Alerting on User Impact | [PR #8](https://github.com/LE0ST/tasklane/pull/8), [incident drill](incident-drill.md) | Real task endpoint 503, metric/log/trace and Grafana firing→resolved; no production fault injection |
| On-Call Agent | [Merged PR #9](https://github.com/LE0ST/tasklane/pull/9), [on-call CI](https://github.com/LE0ST/tasklane/actions/runs/40272231320) | **PARTIAL ONLY:** real Grafana alert wakes local read-only responder; deterministic MockAgentAdapter applies a fixture rollback in a separate TestClient/SQLite drill. No LLM-authored code fix, no proven full rubric outcome |

## Verified production infrastructure — read-only

- **Render:** Web Service `tasklane-app`, Docker runtime, branch `main`, auto-deploy on passing checks (`checksPass`). Latest deployment: `7c7344690bf3260297301a14f434d00cb6eb123a`, `live` (deploy `dep-dbfe8kuk1f9s7386g05g`, completed October 26, 2026 at 05:26 UTC). This is a deployment status observation, not a fresh public HTTP smoke in this documentation update.
- **Neon:** PostgreSQL 16, project `tasklane`, branch `import`, database `neondb`. A read-only SQL aggregate on October 25, 2026 returned `total=2`, `migration_check=1` for title `NEON-MIGRATION-CHECK-20261023`, and Alembic revision `1ea1353bfe87`.
- **Former Render PostgreSQL:** Historical migration comparison was recorded earlier; the source database had an October 24 expiry and its present accessibility and current row count were **not** reverified. **Do not claim that it remains an available backup.**
- Neither query changed Render application settings, Neon rows, production schema, or secrets. The actual production `DATABASE_URL` is never printed or committed.

## Truthful evidence and governance boundaries

- `main` currently points to squash commit `7c73446` after PR #9. The final submission SHA is **still pending**, because Issue #5 documentation PR #10 is not yet merged. Do not confuse PR #9's premerge HEAD (`244e58c`) with the merged squash commit (`7c73446`).
- The Codex adapter exists in the merged local diagnostic code, but **live write-capable Codex remains disabled/unverified**. Non-root, `cwd` and environment filtering do not make an independent OS-level sandbox.
- A full on-call AI code-fix demonstration requires separately reviewed enforced isolation, maintainer authorization and actual model-authored diff + restored running HTTP service. No such result is claimed.
- GitHub shows PRs under the authenticated maintainer account. Agent roles describe work allocation, not separate GitHub identities or accounts.
- Peer-review completion and final course-platform submission remain **unverified**. See [submission checklist](submission-checklist.md).
