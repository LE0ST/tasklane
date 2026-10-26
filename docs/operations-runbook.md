# TaskLane — Deployment, Continuity and Rollback Runbook

**Scope:** educational public demo + production-safe read-only verification. **Verified submission candidate:** commit `96d3ebce69ed76345635fa5a90c82773229e0d9e`; Render Docker Web Service `tasklane-app` on `main`; Neon PostgreSQL 16 branch `import`, database `neondb`.

## 1. Reproduce locally without touching production

Requirements: Docker Engine, Docker Compose, and optional Python 3 for the telemetry smoke scripts. For a complete local app with PostgreSQL 16:

```bash
docker compose up --build -d
```

Browse `http://localhost:8000` and `http://localhost:8000/health`. Application image builds the React frontend and serves it via FastAPI; Compose starts its own **local** PostgreSQL container/volume. Data is not connected to Neon. Stop with `docker compose down`; `docker compose down -v` **deletes only the local Compose database volume**, so use that only for disposable test data.

Optional local metrics, logs, traces and real user-impact 503 drill:

```bash
docker build -t tasklane:hw3 .
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
python3 observability/smoke.py
python3 observability/incident_smoke.py
docker compose -f docker-compose.yml -f docker-compose.observability.yml down
```

[Merged PR #9](https://github.com/LE0ST/tasklane/pull/9) provides an optional `docker-compose.oncall.yml` overlay for local-only **read-only** diagnosis. Use the documented [on-call procedure](https://github.com/LE0ST/tasklane/blob/main/docs/oncall-drill.md). Never treat its mock fixture fix as autonomous AI remediation.

## 2. CI/CD and public health verification

- GitHub Actions CI runs on pull requests to `main` and commits pushed to `main`. Four jobs cover frontend bundle, backend/PostgreSQL migrations/tests, Docker/Playwright and local OpenTelemetry/Grafana real 503 alert lifecycle. Latest verified submission candidate: [Actions run 40350303951](https://github.com/LE0ST/tasklane/actions/runs/40350303951) passed 4/4 for `96d3ebce69ed76345635fa5a90c82773229e0d9e`; Render deploy `dep-dbfn7b4s728c73dq7f90` was verified `live`. Earlier run 40273628271 remains evidence for the `7c73446` on-call change.
- Render service `tasklane-app` is wired to GitHub `main` and configured with `autoDeployTrigger=checksPass`. **No distinct staging promotion gate is configured or claimed.** The Docker entrypoint serves app HTTP; health path is `/health`.
- Public demo: [https://tasklane-app.onrender.com](https://tasklane-app.onrender.com); API documentation: [https://tasklane-app.onrender.com/docs](https://tasklane-app.onrender.com/docs). Free-plan cold starts may delay health responses. **Do not insert synthetic incident headers or write probes into production**.
- The earlier October 26 [verification recorded in Issue #5](https://github.com/LE0ST/tasklane/issues/5#issuecomment-6410875915) records `946b582` as `live` and its public `/health` response as `{"status":"healthy"}`. The verified candidate `96d3ebce69ed76345635fa5a90c82773229e0d9e` passed [CI 40350303951](https://github.com/LE0ST/tasklane/actions/runs/40350303951) and Render recorded its matching deployment `live`; no fresh public health probe was made for that later SHA. No rollback or production write probe was executed.

## 3. Neon connection and persistence verification (read-only)

The Render service uses a protected environment variable `DATABASE_URL` to connect over SSL to Neon PostgreSQL. **Never print it or commit it**. Neon project `tasklane`, branch `import`, DB `neondb`; expected Alembic revision at this baseline is `1ea1353bfe87`.

Use the authorized Neon SQL query interface for read-only verification, or run the following in an already authenticated database session (no credentials shown):

```sql
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE title = 'NEON-MIGRATION-CHECK-20261023') AS migration_check,
       (SELECT version_num FROM public.alembic_version LIMIT 1) AS revision
FROM public.tasks;
```

**Read-only verification through October 26, 2026:** `total=2`, `migration_check=1`, `revision=1ea1353bfe87`. This is an observation of existing data, **not** a newly created test record. Prior migration checks established the import from Render PostgreSQL; current availability/content of the former Render source is unverified. Do not present the former database as a recovery copy.

## 4. Incident and rollback decision tree

1. **Identify** the exact failing production deploy SHA, affected API behavior, and current `/health` status. Check GitHub Actions and Render deploy history. Do not infer a database regression from an app HTTP 500 alone.
2. **Freeze risky changes:** do not submit new production task creations/deletions or schema migrations as a diagnostic experiment; retain logs and commit/deploy IDs.
3. **Compare** the current schema with the last healthy application release. An application rollback can be incompatible with a later schema migration. Check migration history and whether old code supports the current tables/columns.
4. **Application rollback (human-approved action):** from the Render Dashboard, open `tasklane-app` → Deploys, select a known healthy and schema-compatible historical deploy and use the provider rollback/redeploy control **if available**. This must be a deliberate maintainer action, not an automatic agent operation. No rollback was performed by this documentation task.
5. **Verify after rollback:** recheck the public health endpoint and key read-only API behavior; verify Neon row count and Alembic revision with SELECT only. Monitor deploy status. Do not infer data restoration from app recovery.

## 5. Database restoration safety

- Application rollback **never** restores accidentally deleted rows or changes an already-applied Alembic revision.
- Do not automatically run `alembic downgrade` against production. Some downgrades can irreversibly remove columns/tables or application data.
- Prefer tested forward-fixing, additive migrations (expand/contract) after reviewing the deployed schema and code compatibility.
- Before a future risky migration, an authorized maintainer should create and validate a separate logical backup (for example, `pg_dump` to protected storage) or an appropriately supported Neon recovery point. **No backup artifact, snapshot restore or point-in-time recovery has been independently verified for this project.**
- If recovery is necessary, choose an authenticated backup with known retention, test restore into an isolated **nonproduction** database, verify schema/rows, then obtain explicit approval before any production restore. Never replace production `DATABASE_URL` by guesswork.

## 6. Separation of environments

| Environment | Database | Telemetry/alerts | AI repair |
|---|---|---|---|
| Render production | Neon `import` / `neondb` | Optional local OTel flags not enabled | **Not enabled** |
| Standard local Compose | Local PostgreSQL 16 volume | Base application only | Not enabled |
| Optional observability Compose | Separate disposable local PostgreSQL 16 | Prometheus/Loki/Tempo/Grafana, real local 503 | No production writes |
| On-call overlay (merged PR #9; opt-in local-only) | Disposable local stack | Real Grafana→responder read-only triage | Deterministic fixture demo only, **not** real AI remediation |

See [evidence traceability](evidence-traceability.md) and [submission checklist](submission-checklist.md).
