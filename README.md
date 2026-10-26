# TaskLane: Full-Stack Containerized Kanban Board

[![CI](https://github.com/LE0ST/tasklane/actions/workflows/ci.yml/badge.svg)](https://github.com/LE0ST/tasklane/actions/workflows/ci.yml)
[![Live Demo](https://img.shields.io/badge/Render-Live%20Demo-brightgreen)](https://tasklane-app.onrender.com)
[![Course](https://img.shields.io/badge/DataTalks.Club-AI%20Dev%20Tools%20Zoomcamp-blue)](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp)

TaskLane is a publicly deployed educational full-stack Mini Kanban Board. It demonstrates spec-first implementation, an isolated PostgreSQL-backed local stack, automated CI/CD, correlated local observability, and user-impact alerting. The optional on-call feature is under independent review; no AI-generated code repair is claimed.

- **Live Application URL**: [https://tasklane-app.onrender.com](https://tasklane-app.onrender.com)
- **Interactive OpenAPI Docs**: [https://tasklane-app.onrender.com/docs](https://tasklane-app.onrender.com/docs)
- **GitHub Repository**: [https://github.com/LE0ST/tasklane](https://github.com/LE0ST/tasklane)

---

## Project Overview & Evolution

This project was developed across the **AI Dev Tools Zoomcamp 2026** by DataTalks.Club:

1. **Homework 2 (Spec-First Full-Stack Application)**:
   - Defined product requirements and state transitions in `_docs/specs.md`.
   - Designed OpenAPI 3.1 contract (`openapi.yaml`).
   - Implemented FastAPI backend with Pydantic v2 and SQLite persistence.
   - Built React Kanban UI with centralized API abstraction.

2. **Homework 3 (Containerization, PostgreSQL, CI/CD, and Cloud Deployment)**:
   - Upgraded database layer to **PostgreSQL 16** with **Alembic** schema migrations.
   - Built a multi-stage production **Dockerfile** (Node 20 Alpine builder + Python 3.11 slim runtime) serving compiled static assets from FastAPI.
   - Created **Docker Compose** stack with isolated PostgreSQL services and health checks.
   - Implemented automated **PostgreSQL integration tests** and **Playwright E2E browser tests**.
   - Built a 3-stage **GitHub Actions CI/CD** pipeline verifying builds, tests, Compose, and E2E in Chromium.
   - Deployed live to **Render** (Docker Web Service).

3. **Project 1 (Observability, User-Impact Alerting, On-Call Diagnostics, and Production Continuity)**:
   - Migrated production database to **Neon Serverless PostgreSQL 16** (`tasklane` project, **`import`** branch, `neondb` database) to secure database persistence and eliminate Render's 30-day free-tier expiration risk (the former short-lived Render database had an October 24, 2026 expiration date).
   - The former Render PostgreSQL (`tasklane-db`) served only as a temporary reference source for migration verification prior to its expiration.
   - Verified live migration: newly created tasks persist in Neon `neondb` and are absent from the former Render database.
   - Established specialized multi-agent engineering workflow and modular documentation ([`docs/teams.md`](docs/teams.md), [`docs/process.md`](docs/process.md), [`docs/architecture.md`](docs/architecture.md), [`docs/project-roadmap.md`](docs/project-roadmap.md)).
   - Established public issue tracking covering Project 1 deliverables ([GitHub Issues #1–#5](https://github.com/LE0ST/tasklane/issues)).
   - Merged opt-in local OpenTelemetry, Prometheus/Loki/Tempo/Grafana correlation in [PR #7](https://github.com/LE0ST/tasklane/pull/7) and a real task-operation 5xx Grafana firing/recovery drill in [PR #8](https://github.com/LE0ST/tasklane/pull/8).
   - Prepared an isolated read-only on-call diagnosis feature and a **deterministic mock** repair demonstration in [Draft PR #9](https://github.com/LE0ST/tasklane/pull/9), pending independent approval and human merge authorization. A real coding model has **not** been shown to fix the bug.

---

## Architecture Overview

```mermaid
graph TD
    subgraph Client Layer
        Browser[Web Browser / Evaluator]
    end

    subgraph Render Cloud Platform
        subgraph Render Web Service - Docker Multi-Stage
            FastAPI[FastAPI Backend :8000 / Dynamic :10000]
            Static[Mounted React 19 SPA Dist]
            FastAPI --- Static
        end
    end

    subgraph Active Production Database: Neon
        NeonPG[(Neon PostgreSQL 16<br/>Project: tasklane<br/>Branch: import<br/>DB: neondb)]
    end

    subgraph Former Temporary Database: Render
        RenderPG[(Render PostgreSQL 16<br/>tasklane-db<br/>Status: Temporary Reference / Expiring Oct 24, 2026)]
    end

    subgraph CI/CD Pipeline - GitHub Actions
        GHA[Push to main / PR]
        J1[1. Frontend Build & Asset Bundle]
        J2[2. Backend Unit & Postgres Integration Tests]
        J3[3. Docker Build, Smoke Tests & Playwright E2E]
        J4[4. Local OpenTelemetry Correlation & 5xx Alert Drill]
        GHA --> J1
        J1 --> J2
        J2 --> J3
        J2 --> J4
    end

    Browser -->|HTTPS :443| FastAPI
    FastAPI -->|Protected DATABASE_URL (SSL)<br/>Alembic Migrations on Lifespan| NeonPG
```

---

## Technology Stack

| Layer | Technologies | Role / Highlights |
|---|---|---|
| **Frontend** | React 19, Vite 6, CSS Modules | Responsive 5-column Kanban board, live filters, modal dialogs |
| **Backend** | FastAPI, Uvicorn, Pydantic v2 | RESTful API, input validation, static file serving, lifespan hooks |
| **Database & ORM** | Neon PostgreSQL 16 (`import` branch), SQLAlchemy 2.0, Alembic | Active verified serverless persistence, schema migrations (`1ea1353bfe87`) |
| **Former Database** | Render PostgreSQL 16 (`tasklane-db`) | Temporary reference source (expiring October 24, 2026) |
| **Containerization** | Docker, Multi-stage builds, Docker Compose | Node 20 builder + Python 3.11 slim runtime, pinned `uv:0.12.18` |
| **Testing** | `pytest`, `httpx`, `@playwright/test` | 76 backend tests on merged `main`; 9 browser scenarios; 46 additional on-call tests only in Draft PR #9 |
| **CI/CD** | GitHub Actions | Automated builds, migrations, smoke checks, Playwright E2E |
| **Cloud Hosting** | Render Web Service + Neon PostgreSQL | Docker Web Service + Serverless PostgreSQL 16 |

---

## Core Features

- **Deterministic 5-Stage Kanban Workflow**:
  `Backlog` &rarr; `To Do` &rarr; `In Progress` &rarr; `Review` &rarr; `Done`.
- **Enforced Initial Placement**: All new tasks are strictly created in `Backlog`.
- **Sequential Card Controls**: Previous/next status transition buttons (`&larr;` / `&rarr;`), automatically disabled at column boundaries.
- **Search & Filtering**: Real-time filtering by priority (`low`, `medium`, `high`, `urgent`) and substring search across task titles and descriptions.
- **Deterministic Ordering**: Tasks are returned newest first (`created_at DESC`, `id DESC`).
- **Input Validation**: Empty titles, invalid priorities, and illegal client-supplied creation statuses are rejected with `422 Unprocessable Entity`.
- **Health Check Endpoints**: `/health` and `/api/health` returning `{"status": "healthy"}` for container and cloud orchestrators.

---

## Project Structure

```text
tasklane/
├── .github/
│   └── workflows/
│       └── ci.yml                 # 4-job GitHub Actions CI pipeline
├── _docs/
│   └── specs.md                   # Product specifications and acceptance criteria
├── backend/                       # FastAPI + SQLAlchemy + Alembic application
│   ├── alembic/                   # Alembic configuration and migration versions
│   │   ├── versions/
│   │   │   └── 1ea1353bfe87_create_tasks_table.py
│   │   └── env.py
│   ├── tests/
│   │   ├── test_api.py            # API, validation, and task route tests
│   │   └── test_postgres_integration.py # PostgreSQL integration and migration checks
│   ├── alembic.ini                # Alembic CLI configuration
│   ├── database.py                # Database connection, engine setup, URL normalization
│   ├── main.py                    # FastAPI entrypoint, lifespan migrations, static mounts
│   ├── models.py                  # SQLAlchemy Task ORM model
│   ├── pyproject.toml             # uv dependencies and pytest markers
│   ├── schemas.py                 # Pydantic v2 schemas
│   └── uv.lock                    # Locked Python dependency tree
├── frontend/                      # React 19 + Vite application
│   ├── e2e/
│   │   └── kanban.spec.js         # 9 Playwright end-to-end browser scenarios
│   ├── src/
│   │   ├── services/
│   │   │   └── api.js             # Centralized API client abstraction
│   │   ├── App.css                # Responsive Kanban styling
│   │   ├── App.jsx                # Board component and state logic
│   │   ├── index.css              # Global design tokens
│   │   └── main.jsx               # React entrypoint
│   ├── index.html                 # Single page application template
│   ├── package.json               # Node dependencies and scripts
│   ├── package-lock.json          # Locked Node dependencies
│   ├── playwright.config.js       # Playwright E2E configuration
│   └── vite.config.js             # Vite bundler configuration
├── AGENTS.md                      # Agent context and operational guardrails
├── docs/                          # Modular architecture, team, process and evidence guides
├── observability/                 # Optional local metrics/logs/traces + alert drill
├── docker-compose.observability.yml # Optional local telemetry overlay
├── .dockerignore                  # Docker build context exclusions
├── .env.example                   # Environment configuration template
├── .gitignore                     # Git version control exclusions
├── Dockerfile                     # Multi-stage production container definition
├── docker-compose.yml             # Local multi-service PostgreSQL + App stack
├── openapi.yaml                   # OpenAPI 3.1 specification
└── README.md                      # Comprehensive project documentation
```

---

## Local Development & Running Instructions

### Prerequisites
- Docker & Docker Compose **or**:
- Python 3.10+ with [`uv`](https://docs.astral.sh/uv/) and Node.js 20+ with `npm`.

### Option A: Running with Docker Compose (Recommended)

To start the full-stack containerized application with PostgreSQL 16:
```bash
docker compose up --build
```
This command:
1. Builds the multi-stage Docker image packaging React into FastAPI.
2. Starts `postgres:16-alpine` with health checks on `127.0.0.1:5432`.
3. Runs Alembic migrations automatically on startup via FastAPI lifespan.
4. Serves the full-stack application at **`http://localhost:8000`**.

To stop and remove containers:
```bash
docker compose down
# Or to also wipe local volume data:
docker compose down -v
```

### Option B: Running Standalone Outside Docker

1. **Backend**:
   ```bash
   cd backend
   uv sync
   uv run uvicorn main:app --reload --port 8000
   ```
   *(Defaults to local SQLite `tasklane.db` if `DATABASE_URL` is omitted).*

2. **Frontend**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   *Available at `http://localhost:5173` (proxies requests to backend on port 8000).*

---

## Environment Variables Reference

Configure environment variables in `.env` (copied from `.env.example`) or in your deployment dashboard:

| Variable | Required | Default (Local) | Purpose |
|---|---|---|---|
| `DATABASE_URL` | No | `sqlite:///./tasklane.db` (local dev) | Connection string (PostgreSQL or SQLite). Automatically normalizes `postgres://` to `postgresql://`. |
| `POSTGRES_USER`| No | `postgres` | PostgreSQL username in Docker Compose. |
| `POSTGRES_PASSWORD`| No | `postgres` | PostgreSQL password in Docker Compose. |
| `POSTGRES_DB`  | No | `tasklane` | PostgreSQL database name in Docker Compose. |
| `PORT`         | No | `8000` | HTTP port for Uvicorn server (dynamically overridden by cloud platforms). |
| `HOST`         | No | `0.0.0.0` | Bind host address. |
| `VITE_API_BASE_URL` | No | `""` (production) / `http://localhost:8000` (dev) | API base path prefix for frontend. |

> [!WARNING]
> Never commit real secrets or production credentials to Git. Keep `.env` listed in `.gitignore`.

---

## Automated Testing & Continuous Integration

Backend tests can be run from the repository root with:
```bash
cd backend
uv sync --frozen
uv run pytest -v --strict-markers
```

At merged `main` commit `9968999`, [Actions run 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032) passed **76 backend tests** and all four CI jobs. The backend job provisions PostgreSQL 16, creates a dedicated test database, applies Alembic migrations, and runs unit plus integration tests. The browser job tests the running Compose app with Playwright/Chromium (nine E2E scenarios). No production database is used in CI.

Optional local telemetry and real Grafana 5xx firing/recovery are verified using:
```bash
docker build -t tasklane:hw3 .
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
python3 observability/smoke.py
python3 observability/incident_smoke.py
docker compose -f docker-compose.yml -f docker-compose.observability.yml down
```

To reproduce the **on-call diagnosis-only overlay** once [Draft PR #9](https://github.com/LE0ST/tasklane/pull/9) is merged, follow its `docs/oncall-drill.md` and add `-f docker-compose.oncall.yml`. The PR currently reports [four successful jobs and 76 backend + 46 on-call tests](https://github.com/LE0ST/tasklane/actions/runs/40265085450). These PR-specific files and tests are **not yet part of merged main**. Real Grafana-to-responder delivery is separately verified; mock repair does not prove AI-authored remediation.

The [CI workflow](.github/workflows/ci.yml) runs on PRs and pushes to `main` and contains four jobs:

| Job | Verified purpose |
|---|---|
| `frontend-build` | Vite production bundle and artifact |
| `backend-tests` | SQLite/API checks, PostgreSQL integration and Alembic migrations |
| `docker-e2e-smoke` | Docker Compose, HTTP smoke, Playwright Chromium |
| `observability-smoke` | Local Prometheus/Loki/Tempo/Grafana correlation, real task-operation 503 alert firing and resolution |

Render is configured to auto-deploy the `main` branch after passing checks. That is automatic production deployment after CI, **not** a distinct development staging environment plus manual production promotion. See [the deployment and recovery runbook](docs/operations-runbook.md).

---

## Cloud Deployment (Render & Neon)

TaskLane is publicly deployed on **Render** with persistent serverless storage on **Neon**:
- **Application URL**: [https://tasklane-app.onrender.com](https://tasklane-app.onrender.com)
- **Deployment Mode**: Render Web Service (Docker Runtime) connected directly to `LE0ST/tasklane` (`main` branch).
- **Active Production Database**: **Neon Serverless PostgreSQL 16** (`tasklane` project, **`import`** branch, `neondb` database).
- **Former Source Database**: Render Managed PostgreSQL 16 (`tasklane-db`) was used as a historical migration reference and had an October 24, 2026 expiry. Its current availability was not reverified and it is **not** a backup.
- **Production Migration Verification**: A prior migration test confirmed new tasks in Neon rather than the then-available Render source database. A fresh **read-only** Neon query on October 25 confirmed two tasks, including the original migration-check task, and Alembic revision `1ea1353bfe87`. No fresh query against the former Render database was performed.
- **Secure Networking**: Web Service communicates with Neon over SSL via a protected `DATABASE_URL` environment variable.
- **Automatic Migrations**: Alembic migrations run automatically on container startup inside the FastAPI lifespan handler.
- **Graceful Shutdown**: The Dockerfile entrypoint uses `exec uvicorn` to receive POSIX `SIGTERM` signals directly from Render.

### Operational Considerations & Continuity
- **Cold starts**: Free web services may sleep during inactivity and take longer to answer their first request. Do not assume a fixed wake-up time; allow time for the public health endpoint to respond.
- **Plan quotas**: Free plans and cloud quotas may change; verify live limits in each provider dashboard.
- **Database Continuity**: Neon Serverless PostgreSQL removes Render Free's fixed 30-day expiration schedule, mitigating the imminent October 24 database shutdown risk for project submission and peer review (cloud free-tier services remain subject to standard platform quotas and idle behavior).
- **Public Demonstration Disclaimer**: This deployment is an unauthenticated educational demonstration. Do not submit sensitive, personal, or confidential information.

---

## Project 1 Modular Documentation & Public Issue Tracker

TaskLane's AI-assisted development workflow for Project 1 is fully documented across modular specifications:

| Resource | Link | Summary |
|:---|:---|:---|
| **Agent Entry Point** | [`AGENTS.md`](AGENTS.md) | Central entry point and operational manual for development agents |
| **Agent Team Specification** | [`docs/teams.md`](docs/teams.md) | Agent roster (Luna, Flash, Sol, Astra, Argon, Maintainer), RACI matrix, and boundaries |
| **Engineering Process** | [`docs/process.md`](docs/process.md) | Verified 13-stage delivery lifecycle, review handoffs, and no-fabrication policy |
| **System Architecture** | [`docs/architecture.md`](docs/architecture.md) | Concrete system topology, container layers, and Neon database integration |
| **Local Observability Demo** | [`docs/observability.md`](docs/observability.md) | Optional OTel stack, reproducible CI correlation evidence, metrics/logs/traces and Grafana instructions |
| **Local Incident Drill** | [`docs/incident-drill.md`](docs/incident-drill.md) | Versioned Grafana 5xx alert and webhook, guarded 503 injection, reproducible firing/recovery evidence |
| **On-Call Responder (pending)** | [Draft PR #9](https://github.com/LE0ST/tasklane/pull/9) | Real Grafana-triggered diagnosis and independent mock-only repair evidence; do not claim AI-generated remediation |
| **Evidence & Traceability** | [`docs/evidence-traceability.md`](docs/evidence-traceability.md) | Issue → PR → commit → CI chain and evidence boundaries |
| **Operations & Recovery** | [`docs/operations-runbook.md`](docs/operations-runbook.md) | Render/Neon verification, rollback decision tree and DB safety |
| **Submission Checklist** | [`docs/submission-checklist.md`](docs/submission-checklist.md) | Exact submission SHA, peer review obligations and remaining gates |
| **Project Roadmap** | [`docs/project-roadmap.md`](docs/project-roadmap.md) | Milestones, rubric criteria mapping, and tracking of Issues #1–#5 |
| **Product Specification** | [`_docs/specs.md`](_docs/specs.md) | Canonical acceptance criteria (AC-1..AC-10) and validation rules |
| **OpenAPI Specification** | [`openapi.yaml`](openapi.yaml) | OpenAPI 3.1 REST API contract |

### Public GitHub Issue and Pull Request Status (verified October 25, 2026)

| Issue | Status | Evidence |
|---|---|---|
| [#1 — Team, process, modular docs](https://github.com/LE0ST/tasklane/issues/1) | Closed | [PR #6](https://github.com/LE0ST/tasklane/pull/6), merged `f6f4c7b` |
| [#2 — Correlated observability](https://github.com/LE0ST/tasklane/issues/2) | Closed | [PR #7](https://github.com/LE0ST/tasklane/pull/7), merged `cc9cce7` |
| [#3 — User-impact alert and drill](https://github.com/LE0ST/tasklane/issues/3) | Closed | [PR #8](https://github.com/LE0ST/tasklane/pull/8), merged `9968999` |
| [#4 — Bounded on-call responder](https://github.com/LE0ST/tasklane/issues/4) | Open | [Draft PR #9](https://github.com/LE0ST/tasklane/pull/9), security/SRE audit pending; AI-generated fix not proven |
| [#5 — Final audit and evidence](https://github.com/LE0ST/tasklane/issues/5) | Open | Documentation/evidence work in progress; final submission SHA pending |

See [the detailed traceability matrix](docs/evidence-traceability.md) for CI links, reviewer handoffs, and precise claims.

---

## Operations, Rollback & Submission

Production uses the Render Docker Web Service `tasklane-app` and Neon PostgreSQL 16 (`import` branch, `neondb`). Render currently follows `main` and deploys after CI checks pass. The latest verified live `main` deployment for this documentation baseline is `9968999`.

**Application rollback is not database rollback.** First select a previously healthy compatible commit/deploy in the Render dashboard, check application health and compatibility with the current Alembic schema, then verify read-only database state. Never trigger `alembic downgrade` or restore production data as an automatic response to a bad app deploy. There is no independently verified backup snapshot claimed here.

See [Operations & Recovery](docs/operations-runbook.md) and [Submission Checklist](docs/submission-checklist.md). No rollback or production mutation was executed while preparing this documentation.

**Course peer review requirement:** three reviews of other participants are mandatory according to the [official project criteria](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md). Completion has not yet been verified. Exact submission deadline and workflow must be confirmed on the course platform.
