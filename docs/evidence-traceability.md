# TaskLane — Project 1 Evidence and Traceability Matrix

**As-of date:** October 25, 2026 (Lima). **Baseline:** merged `main` commit [`9968999`](https://github.com/LE0ST/tasklane/commit/996899924f04d3abe02ca5fd1d1578ad06e554c8). **Status:** documentation prepared separately from the still-unmerged on-call Draft PR #9.

This is an auditable index, **not** a claim of grades awarded by peer reviewers. Source evidence is public issue/PR/CI history plus read-only cloud checks. External AI audit summaries are recorded as PR discussion comments, not represented as native GitHub approval reviews.

## Delivery chain: issue → PR → merged commit → CI

| Workstream | Tracker | Pull request and result | Verified evidence |
|---|---|---|---|
| Agent workflow and modular context | [Issue #1](https://github.com/LE0ST/tasklane/issues/1) — closed | [PR #6](https://github.com/LE0ST/tasklane/pull/6) — squash `f6f4c7b` | [AGENTS.md](../AGENTS.md), [teams](teams.md), [process](process.md), [architecture](architecture.md) |
| Real local OpenTelemetry | [Issue #2](https://github.com/LE0ST/tasklane/issues/2) — closed | [PR #7](https://github.com/LE0ST/tasklane/pull/7) — squash `cc9cce7` | [observability guide](observability.md), correlated metrics/logs/traces and Grafana Explore |
| Task-operation HTTP 5xx alert | [Issue #3](https://github.com/LE0ST/tasklane/issues/3) — closed | [PR #8](https://github.com/LE0ST/tasklane/pull/8) — squash `9968999` | [local 503 drill](incident-drill.md); [merged-main CI 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032), 4/4 jobs, 76 backend tests |
| On-call diagnostic receiver | [Issue #4](https://github.com/LE0ST/tasklane/issues/4) — open | [Draft PR #9](https://github.com/LE0ST/tasklane/pull/9), **not merged**, HEAD `ef53463` | [PR CI 40265085450](https://github.com/LE0ST/tasklane/actions/runs/40265085450), 4/4 jobs, 76 backend + 46 responder tests; real Grafana delivery and correlation, **mock-only repair** |
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
| Testing | [CI workflow](../.github/workflows/ci.yml), [Actions 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032) | 76 backend tests on merged main; PR #9 has 46 additional responder tests |
| Containerization | [Dockerfile](../Dockerfile), [one-command Compose](../docker-compose.yml) | Base stack includes local PostgreSQL 16; optional observability is a separate overlay |
| Deployment | [Render public URL](https://tasklane-app.onrender.com), [operations runbook](operations-runbook.md) | Last observed deploy marked live at `9968999`; no write probe was sent to production |
| CI/CD | [Actions workflow](../.github/workflows/ci.yml), Render `main` autoDeployTrigger=`checksPass` | Automatic production deployment; **no separate staging environment or manual production promotion** is claimed |
| Observability | [OTel guide](observability.md), [PR #7](https://github.com/LE0ST/tasklane/pull/7) | Local-only Prometheus/Loki/Tempo/Grafana, not production telemetry |
| Alerting on User Impact | [PR #8](https://github.com/LE0ST/tasklane/pull/8), [incident drill](incident-drill.md) | Real task endpoint 503, metric/log/trace and Grafana firing→resolved; no production fault injection |
| On-Call Agent | [Draft PR #9](https://github.com/LE0ST/tasklane/pull/9), [on-call CI](https://github.com/LE0ST/tasklane/actions/runs/40265085450) | **PARTIAL ONLY:** real Grafana alert wakes local read-only responder; deterministic MockAgentAdapter applies a fixture rollback in a separate TestClient/SQLite drill. No LLM-authored code fix, no proven full rubric outcome |

## Verified production infrastructure — read-only

- **Render:** Web Service `tasklane-app`, Docker runtime, branch `main`, auto-deploy on passing checks (`checksPass`). Last listed deploy: `996899924f04d3abe02ca5fd1d1578ad06e554c8` with `live` status and completed October 25, 2026 at 22:11 UTC. This is a deployment status observation, not a fresh public HTTP smoke in this documentation update.
- **Neon:** PostgreSQL 16, project `tasklane`, branch `import`, database `neondb`. A read-only SQL aggregate on October 25, 2026 returned `total=2`, `migration_check=1` for title `NEON-MIGRATION-CHECK-20261023`, and Alembic revision `1ea1353bfe87`.
- **Former Render PostgreSQL:** Historical migration comparison was recorded earlier; the source database had an October 24 expiry and its present accessibility and current row count were **not** reverified. **Do not claim that it remains an available backup.**
- Neither query changed Render application settings, Neon rows, production schema, or secrets. The actual production `DATABASE_URL` is never printed or committed.

## Truthful evidence and governance boundaries

- `main` is the submitted baseline until a later explicitly authorized merge. **Do not cite the Draft PR #9 SHA as a deployed production SHA or as final submission SHA.**
- The CodeX adapter is present in a review branch but **live write-capable Codex remains disabled/unimplemented**. Non-root, `cwd` and environment filtering do not make an independent OS-level sandbox.
- A full on-call AI code-fix demonstration requires separately reviewed enforced isolation, maintainer authorization and actual model-authored diff + restored running HTTP service. No such result is claimed.
- GitHub shows PRs under the authenticated maintainer account. Agent roles describe work allocation, not separate GitHub identities or accounts.
- Peer-review completion and final course-platform submission remain **unverified**. See [submission checklist](submission-checklist.md).
