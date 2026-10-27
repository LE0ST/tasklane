# TaskLane — Project 1 Evidence and Traceability Matrix

**As-of date:** October 26, 2026 (Lima). **Historical verified submission candidate:** [`96d3ebc`](https://github.com/LE0ST/tasklane/commit/96d3ebce69ed76345635fa5a90c82773229e0d9e). [CI 40350303951](https://github.com/LE0ST/tasklane/actions/runs/40350303951) passed 4/4; Render deploy `dep-dbfn7b4s728c73dq7f90` was verified `live`. No new public `/health` probe is claimed for this docs-only SHA. The earlier `946b582` baseline and its health verification remain historical evidence. PR #9 has passed external SRE/security review for its restricted diagnosis-only scope and was merged with explicit maintainer authorization. Deployment and postmerge CI checks are tracked separately.

This is an auditable index, **not** a claim of grades awarded by peer reviewers. The merged baseline uses public issue/PR/CI history and read-only cloud checks; the feature-branch experiment below is a separate maintainer report. External AI audit summaries are recorded as PR discussion comments, not represented as native GitHub approval reviews.

## Delivery chain: issue → PR → merged commit → CI

| Workstream | Tracker | Pull request and result | Verified evidence |
|---|---|---|---|
| Agent workflow and modular context | [Issue #1](https://github.com/LE0ST/tasklane/issues/1) — closed | [PR #6](https://github.com/LE0ST/tasklane/pull/6) — squash `f6f4c7b` | [AGENTS.md](../AGENTS.md), [teams](teams.md), [process](process.md), [architecture](architecture.md) |
| Real local OpenTelemetry | [Issue #2](https://github.com/LE0ST/tasklane/issues/2) — closed | [PR #7](https://github.com/LE0ST/tasklane/pull/7) — squash `cc9cce7` | [observability guide](observability.md), correlated metrics/logs/traces and Grafana Explore |
| Task-operation HTTP 5xx alert | [Issue #3](https://github.com/LE0ST/tasklane/issues/3) — closed | [PR #8](https://github.com/LE0ST/tasklane/pull/8) — squash `9968999` | [local 503 drill](incident-drill.md); [merged-main CI 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032), 4/4 jobs, 76 backend tests |
| On-call diagnostic receiver | [Issue #4](https://github.com/LE0ST/tasklane/issues/4) — open (partial acceptance) | [PR #9](https://github.com/LE0ST/tasklane/pull/9), merged squash `7c73446` | [PR CI 40272231320](https://github.com/LE0ST/tasklane/actions/runs/40272231320), 4/4 jobs, 76 backend + 54 responder tests; real Grafana delivery, read-only diagnosis, **mock-only repair**. [merged-main CI 40273628271](https://github.com/LE0ST/tasklane/actions/runs/40273628271) verified 4/4 success and Render live at the matching squash SHA. |
| Final verifiability and submission | [Issue #5](https://github.com/LE0ST/tasklane/issues/5) — open | [PR #10](https://github.com/LE0ST/tasklane/pull/10) merged `300f4e1`; [PR #11](https://github.com/LE0ST/tasklane/pull/11) merged `946b582` | Verified candidate `96d3ebce69ed76345635fa5a90c82773229e0d9e`; [CI 40350303951](https://github.com/LE0ST/tasklane/actions/runs/40350303951) 4/4 and matching Render deploy `dep-dbfn7b4s728c73dq7f90` `live`. Earlier health-check record at `946b582` remains historical. Submission saved and updatable per maintainer, not independently verified; saved SHA and three peer reviews unverified |

## Official rubric — evidence pointers (12 criteria, 24 available points)

The [official course project rubric](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md) requires clear English documentation and **three peer reviews**. It gives the on-call criterion 2 points only if an agent wakes on an alert **and fixes** the issue. Therefore the deterministic mock repair does not establish those 2 points.

| Criterion | Public evidence | Limitation or evidence boundary |
|---|---|---|
| Problem Description | [README overview](../README.md#project-overview--evolution), [functional spec](../_docs/specs.md) | Reviewer assigns points |
| Product Specification | [spec](../_docs/specs.md), [OpenAPI](../openapi.yaml), [Issues #1–#5](https://github.com/LE0ST/tasklane/issues) | Acceptance criteria tracked in public issues |
| Agent Team Specification | [roles](teams.md), [13-stage process](process.md), PR review handoffs | External AI review comments are not formal GitHub approvals |
| Context Management | [AGENTS.md](../AGENTS.md) links modular docs | Reviewed by course peers |
| Application | [public Render demo](https://tasklane-app.onrender.com), [backend](../backend/main.py), [frontend](../frontend/src/App.jsx) | UI-to-API-to-Neon persistence evidence from historical migration and current read-only DB verification |
| Testing | [CI workflow](../.github/workflows/ci.yml), [Actions 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032) | 76 backend + 54 responder tests in PR #9 CI, verified again at merged-main [CI 40273628271](https://github.com/LE0ST/tasklane/actions/runs/40273628271) (4/4 passed) |
| Containerization | [Dockerfile](../Dockerfile), [one-command Compose](../docker-compose.yml) | Base stack includes local PostgreSQL 16; optional observability is a separate overlay |
| Deployment | [Render public URL](https://tasklane-app.onrender.com), [operations runbook](operations-runbook.md) | Render `live` at verified candidate `96d3ebc`; CI 40350303951 passed 4/4. Public health response is documented for earlier `946b582`; no production write probes |
| CI/CD | [Actions workflow](../.github/workflows/ci.yml), Render `main` autoDeployTrigger=`checksPass` | Automatic production deployment; **no separate staging environment or manual production promotion** is claimed |
| Observability | [OTel guide](observability.md), [PR #7](https://github.com/LE0ST/tasklane/pull/7) | Local-only Prometheus/Loki/Tempo/Grafana, not production telemetry |
| Alerting on User Impact | [PR #8](https://github.com/LE0ST/tasklane/pull/8), [incident drill](incident-drill.md) | Real task endpoint 503, metric/log/trace and Grafana firing→resolved; no production fault injection |
| On-Call Agent | [Merged PR #9](https://github.com/LE0ST/tasklane/pull/9), [on-call CI](https://github.com/LE0ST/tasklane/actions/runs/40272231320) | **PARTIAL ONLY:** real Grafana alert wakes local read-only responder; deterministic MockAgentAdapter applies a fixture rollback in a separate TestClient/SQLite drill. A separate supervised live-provider diff and disposable-workspace application are maintainer-reported at feature-branch `2cc5251`; no alert-triggered autonomous fix or proven full rubric outcome |

## Verified production infrastructure — read-only

- **Render:** Web Service `tasklane-app`, Docker runtime, branch `main`, auto-deploy on passing checks (`checksPass`). Render deploy `dep-dbfn7b4s728c73dq7f90` for candidate `96d3ebce69ed76345635fa5a90c82773229e0d9e` is `live` (completed October 26, 2026 at 15:37:50 UTC). [CI 40350303951](https://github.com/LE0ST/tasklane/actions/runs/40350303951) passed 4/4. The earlier public `/health` success at `946b582` is in [Issue #5](https://github.com/LE0ST/tasklane/issues/5#issuecomment-6410875915); no newer health probe is claimed.
- **Neon:** PostgreSQL 16, project `tasklane`, branch `import`, database `neondb`. Read-only SQL aggregates through October 26, 2026 returned `total=2`, `migration_check=1` for title `NEON-MIGRATION-CHECK-20261023`, and Alembic revision `1ea1353bfe87`.
- **Former Render PostgreSQL:** Historical migration comparison was recorded earlier; the source database had an October 24 expiry and its present accessibility and current row count were **not** reverified. **Do not claim that it remains an available backup.**
- Neither query changed Render application settings, Neon rows, production schema, or secrets. The actual production `DATABASE_URL` is never printed or committed.

## Truthful evidence and governance boundaries

- The historical verified submission candidate is `96d3ebce69ed76345635fa5a90c82773229e0d9e`. The maintainer confirms the submission is saved and the platform allows updates; neither was independently verified, and the currently saved SHA is unknown. If choosing a later documentation revision, use its exact merged SHA after its own checks; do not confuse a feature-branch HEAD with its squash-merge commit.
- The Codex adapter exists in the merged local diagnostic code, but **live write-capable Codex remains disabled/unverified**. Non-root, `cwd` and environment filtering do not make an independent OS-level sandbox.
- A separate [maintainer-reported supervised live run](oncall-drill.md#maintainer-reported-supervised-live-run) at local feature-branch SHA `2cc525107d2cf977e8c9dc9e0bf68b0f478b5a65` reports a live-provider response, human-approved disposable-workspace patch, and `after=passed`. No independent run artifact was supplied. It does not establish an alert-triggered autonomous fix or independently isolated verification; it is not merged-main or CI evidence.
- GitHub shows PRs under the authenticated maintainer account. Agent roles describe work allocation, not separate GitHub identities or accounts.
- Peer-review completion and the exact SHA saved in the course platform remain **unverified**; submission and update capability are maintainer-confirmed only. See [submission checklist](submission-checklist.md).
