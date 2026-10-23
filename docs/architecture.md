# TaskLane — System Architecture & Infrastructure

This document describes the concrete, verified system architecture, component boundaries, and infrastructure topology of **TaskLane** for **Project 1 (AI Dev Tools Zoomcamp 2026)**.

---

## 1. System Topology Overview

TaskLane is an AI-assisted full-stack Kanban workflow application deployed in a modern containerized cloud environment:

```mermaid
graph TD
    subgraph Client Layer
        Browser["User / Evaluator Browser"]
    end

    subgraph Render Cloud Platform
        subgraph Web Service: tasklane-app (Docker Multi-Stage)
            FastAPI["FastAPI Backend (:8000 / Dynamic :10000)"]
            Static["Mounted React 19 Static SPA (/dist)"]
            FastAPI --- Static
        end
    end

    subgraph Serverless Cloud Database: Neon
        NeonPG[("Neon PostgreSQL 16<br/>Project: tasklane<br/>Branch: import (Active)<br/>DB: neondb")]
    end

    subgraph Former Temporary Database: Render
        RenderPG[("Render PostgreSQL 16<br/>tasklane-db (Former Source)<br/>Status: Temporary Reference / Expiring Oct 24, 2026")]
    end

    subgraph Local Development Stack (Docker Compose)
        LocalApp["tasklane:hw3 (FastAPI + React)"]
        LocalPG[("postgres:16-alpine (Local :5432)")]
        LocalApp -->|DATABASE_URL| LocalPG
    end

    Browser -->|HTTPS :443| FastAPI
    FastAPI -->|Protected DATABASE_URL (SSL)<br/>Alembic Migrations on Lifespan| NeonPG
```

---

## 2. Infrastructure Components

### 2.1 Production Hosting (Render Web Service)
- **Service Name:** `tasklane-app`
- **Public URL:** [https://tasklane-app.onrender.com](https://tasklane-app.onrender.com)
- **Runtime:** Docker Multi-stage image:
  - **Stage 1 (Frontend Builder):** Node.js 20 Alpine compiles React 19 source into production assets via Vite.
  - **Stage 2 (Runtime):** Python 3.11 slim runtime with `uv:0.12.18` installing locked dependencies from `pyproject.toml` and `uv.lock`. Static assets mounted directly to FastAPI.
- **Process Management:** Entrypoint executes `exec uvicorn main:app` to ensure POSIX `SIGTERM` signals from Render orchestrators trigger graceful shutdowns.
- **Lifespan Startup:** On container startup, FastAPI executes `run_migrations()` running `alembic upgrade head` before serving HTTP traffic.

### 2.2 Production Database (Neon Serverless PostgreSQL 16)
- **Provider:** Neon Serverless PostgreSQL
- **Project Name:** `tasklane`
- **Active Branch:** `import` *(Notice: The active production database resides on the `import` branch, NOT the default `production` branch)*
- **Database Name:** `neondb`
- **Engine Version:** PostgreSQL 16 (SSL required)
- **Connectivity:** Connects over SSL using a protected `DATABASE_URL` environment variable configured in Render's dashboard.
- **Migration & Verification Status:**
  - Production data and schema were migrated from Render's initial database into Neon's `import` branch.
  - The migration was verified live: newly created tasks persist in Neon `neondb` and are absent from the former Render database.
  - Neon removes Render Free's fixed 30-day expiration schedule, mitigating the risk of database shutdown during project submission and peer evaluation (cloud resources remain subject to standard platform service quotas and cold-start sleep behavior).

### 2.3 Former Source Database (Render Managed PostgreSQL)
- **Service Name:** `tasklane-db`
- **Role:** Former initial database source from Homework 3.
- **Status:** Served as a temporary reference source during the migration to verify that new tasks are stored in Neon `neondb` and absent from the old database.
- **Expiration:** As a Render Free database instance, it is scheduled to expire on October 24, 2026. It is NOT a permanent backup. Neon PostgreSQL 16 (`import` branch, `neondb`) is the sole verified production database.

---

## 3. Application Architecture & Data Flow

### 3.1 Backend Application Layer (`backend/`)
- **Framework:** FastAPI with Uvicorn ASGI server.
- **Validation:** Pydantic v2 schemas (`backend/schemas.py`) enforcing strict input bounds and output serialization:
  - Task titles: 1–120 characters, stripped, non-empty.
  - Allowed statuses: `backlog`, `todo`, `in_progress`, `review`, `done`.
  - Allowed priorities: `low`, `medium`, `high`, `urgent`.
- **Database ORM:** SQLAlchemy 2.0 with declarative mapping (`backend/models.py`). Connection engine configured with `pool_pre_ping=True` to verify connection liveness prior to checkout.
- **Schema Migrations:** Alembic (`backend/alembic/`) with migration version `1ea1353bfe87_create_tasks_table.py`.

### 3.2 Frontend Application Layer (`frontend/`)
- **Framework:** React 19 Single Page Application bundled with Vite 6.
- **Styling:** CSS Modules with CSS custom properties (design tokens) for responsive layout across desktop and mobile.
- **Centralized API Client:** `frontend/src/services/api.js` encapsulates all HTTP requests to `/api/tasks`, supporting both production relative paths and local development proxying.
- **Deterministic UI Controls:**
  - 5 dedicated lane columns: Backlog, To Do, In Progress, Review, Done.
  - Explicit directional buttons (`←` / `→`) for sequential lane transitions. Buttons are dynamically disabled at lane boundaries (`backlog` cannot move left; `done` cannot move right).
  - Search filter (substring query across title/description) and priority filter (all, low, medium, high, urgent).

---

## 4. API Design & OpenAPI Contract

The application API surface comprises the formal OpenAPI 3.1 task contract alongside runtime health probe endpoints:

### 4.1 OpenAPI Contract (`openapi.yaml`)
The core task management endpoints are formally specified in [`openapi.yaml`](../openapi.yaml):

| HTTP Method | Route | Description | Request Body | Response Codes |
|:---|:---|:---|:---|:---|
| **GET** | `/api/tasks` | List tasks (newest first) | Query: `status`, `priority`, `search` | `200 OK` (`List[TaskResponse]`) |
| **POST** | `/api/tasks` | Create new task (enforced in `backlog`) | `{title, description?, priority?}` | `201 Created`, `422 Unprocessable Entity` |
| **GET** | `/api/tasks/{task_id}` | Retrieve task detail by ID | None | `200 OK`, `404 Not Found` |
| **PATCH** | `/api/tasks/{task_id}` | Update task fields / transition status | `{title?, description?, status?, priority?}` | `200 OK`, `404 Not Found`, `422 Unprocessable` |
| **DELETE** | `/api/tasks/{task_id}` | Remove task permanently | None | `204 No Content`, `404 Not Found` |

### 4.2 Runtime Health Endpoints (FastAPI)
For cloud platforms and container orchestrators, FastAPI additionally exposes unauthenticated runtime liveness probes (implemented in `backend/main.py`):

| HTTP Method | Route | Description | Response Body |
|:---|:---|:---|:---|
| **GET** | `/health` | Cloud/container health probe | `{"status":"healthy"}` |
| **GET** | `/api/health` | API alias health probe | `{"status":"healthy"}` |

### Core Contract Constraints
1. **Creation Rule:** Tasks created via `POST /api/tasks` are always placed in `backlog`. Client-supplied `status` values on creation are rejected with `HTTP 422`.
2. **Deterministic Ordering:** Tasks are sorted newest first (`created_at DESC`, `id DESC`).
3. **Database Agnosticism:** Queries use standard ANSI SQL via SQLAlchemy ORM, verified across SQLite (dev/test) and PostgreSQL 16 (CI and Neon production).

---

## 5. Telemetry & Observability Architecture (Planned for Issue #2)

Under Project 1 Issue #2, the application architecture will be augmented with OpenTelemetry instrumentation:

```mermaid
graph LR
    subgraph Application Container
        FastAPIApp["FastAPI Service (:8000)"]
        OTelSDK["OpenTelemetry Python SDK<br/>(Tracer, Meter, Logger)"]
        FastAPIApp --> OTelSDK
    end

    subgraph Observability Services (Docker Compose)
        OTelCollector["OpenTelemetry Collector (:4317/:4318)"]
        Prometheus["Prometheus (:9090)<br/>Metrics Storage"]
        Loki["Grafana Loki (:3100)<br/>Log Aggregation"]
        Tempo["Grafana Tempo (:3200)<br/>Trace Storage"]
        Grafana["Grafana UI (:3000)<br/>Dashboards & Alerts"]

        OTelSDK -->|OTLP gRPC/HTTP| OTelCollector
        OTelCollector -->|Export Metrics| Prometheus
        OTelCollector -->|Export Logs| Loki
        OTelCollector -->|Export Traces| Tempo
        Prometheus --> Grafana
        Loki --> Grafana
        Tempo --> Grafana
    end

    subgraph Incident Response (Issue #4)
        Responder["Incident Responder (:8001)"]
        Grafana -->|Webhook POST /alerts| Responder
    end
```

- **Metrics:** `http_requests_total` counter tracking method, route, and status code (bounded labels only; no PII or user IDs).
- **Traces:** Request context propagation correlating endpoint handling and database operations via unique `trace_id`.
- **Logs:** Structured logs linked to `trace_id` and `span_id`.
