# TaskLane — Agent Instructions & Context

Welcome to the TaskLane codebase. This document serves as the primary entry point and operational manual for all autonomous and human engineering agents working on **Project 1 (AI Dev Tools Zoomcamp 2026)**.

---

## 1. Project Overview & Evolution

TaskLane is a production-grade, containerized Mini Kanban Board application:
- **Core Workflow:** Implements a deterministic, sequential 5-stage task lifecycle:
  `backlog` &rarr; `todo` &rarr; `in_progress` &rarr; `review` &rarr; `done`
- **Full-Stack Architecture:** React 19 SPA frontend, FastAPI backend with Pydantic v2 schemas, and SQLAlchemy 2.0 ORM persistence.
- **Production Infrastructure:** Deployed as a multi-stage Docker container on **Render Web Service** (`tasklane-app`) backed by a persistent serverless **Neon PostgreSQL 16** database (`tasklane` project, **`import`** branch, `neondb` database).
- **Public Application:** [https://tasklane-app.onrender.com](https://tasklane-app.onrender.com)
- **Public Tracker:** [https://github.com/LE0ST/tasklane/issues](https://github.com/LE0ST/tasklane/issues)

---

## 2. Modular Documentation Directory

All agents must consult the appropriate modular documentation before planning or applying changes:

| Document | Path | Purpose |
|:---|:---|:---|
| **Product Specification** | [`_docs/specs.md`](file:///G:/PYTHON/tasklane/_docs/specs.md) | Canonical functional requirements, data models, validation rules, and acceptance criteria (AC-1..AC-10). |
| **Agent Team & Roles** | [`docs/teams.md`](file:///G:/PYTHON/tasklane/docs/teams.md) | Agent roster (Luna, Flash, Sol, Astra, Argon, Maintainer), RACI matrix, and write boundaries. |
| **Engineering Process** | [`docs/process.md`](file:///G:/PYTHON/tasklane/docs/process.md) | The 9-stage issue-driven development lifecycle, test verification, review gates, and no-fabrication policy. |
| **System Architecture** | [`docs/architecture.md`](file:///G:/PYTHON/tasklane/docs/architecture.md) | Concrete topology, container layers, Neon PostgreSQL 16 integration, and planned telemetry architecture. |
| **Project Roadmap** | [`docs/project-roadmap.md`](file:///G:/PYTHON/tasklane/docs/project-roadmap.md) | Milestones, scoring criteria mapping, and tracking of GitHub Issues #1–#5. |
| **OpenAPI Contract** | [`openapi.yaml`](file:///G:/PYTHON/tasklane/openapi.yaml) | Formal OpenAPI 3.1 REST API specification. |

---

## 3. Standard Development Commands

### Backend
```bash
# Run backend locally with live reload (defaults to SQLite tasklane.db if DATABASE_URL unset):
cd backend && uv run uvicorn main:app --reload --port 8000

# Run all unit/API tests (14 tests):
cd backend && uv run pytest -v -m "not integration"

# Run PostgreSQL integration tests (requires local PostgreSQL or TEST_DATABASE_URL):
cd backend && uv run pytest -v -m "integration"

# Apply Alembic database migrations:
cd backend && uv run alembic upgrade head
```

### Frontend
```bash
# Install dependencies:
cd frontend && npm ci

# Run development server with backend proxy:
cd frontend && npm run dev

# Compile production bundle:
cd frontend && npm run build

# Run Playwright E2E browser tests:
cd frontend && npm run test:e2e
```

### Containerized Full-Stack Stack
```bash
# Start full application + PostgreSQL 16 in one command:
docker compose up --build -d

# Check service health and logs:
docker compose ps
docker compose logs -f app

# Tear down containers:
docker compose down
```

---

## 4. Core Rules & Constraints (Preserved Baseline)

1. **Language:** Everything in this project must be strictly in **English** (code, variables, comments, docstrings, UI copy, commit messages, and documentation).
2. **Scope Discipline:** Keep the implementation lean and avoid over-engineering. No drag-and-drop libraries; use sequential next/previous buttons (`←` / `→`).
3. **Creation Rule:** New tasks always start in `backlog`. The `POST /api/tasks` endpoint must not allow or require setting a `status`. Status transitions occur exclusively through `PATCH /api/tasks/{id}`.
4. **API Contract:** All communication between frontend and backend must adhere strictly to `openapi.yaml`.
5. **Persistence:** Use SQLAlchemy 2.0 with standard, database-agnostic ORM queries. Support dual compatibility: serverless PostgreSQL 16 in production/CI and local SQLite (`tasklane.db`) for lightweight development.
6. **Testing:** All endpoints and edge cases (creation, listing, search/filter, update, delete, 422 validations, 404s) must remain covered with automated tests. Zero test regressions permitted.

---

## 5. Agent Safety & Working Guardrails

- **Branch Discipline:** Implementation agents must work exclusively inside dedicated feature branches (`project1/<feature-name>`). Committing or pushing directly to `main` is strictly prohibited by project engineering policy.
- **Secret Protection:** Never retrieve, echo, print, or commit connection strings (`DATABASE_URL`), cloud passwords, or API tokens. Keep `.env` ignored.
- **Production Isolation:** Under no circumstances should automated agents modify cloud infrastructure, run destructive test scripts against `https://tasklane-app.onrender.com`, or alter the live Neon database directly.
- **Former Database Expiration:** The former Render database (`tasklane-db`) was a temporary reference source expiring on October 24, 2026. Neon PostgreSQL 16 (`import` branch, `neondb`) is the sole verified production database. Do not attempt to connect to, mutate, or drop the expiring Render database.
- **Sandboxed Incident Responders:** Any on-call automation (Issue #4) must default strictly to read-only mode (`-s read-only`). The `--dangerously-bypass-approvals-and-sandbox` flag is strictly forbidden.
- **Human Approval Gate:** Merges to `main` require explicit review and authorization by the Human Maintainer (`LE0ST`).
