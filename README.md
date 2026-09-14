# TaskLane: Mini Kanban Board

TaskLane is a lightweight, responsive Mini Kanban Board application designed for streamlined, distraction-free task management.

This project was built for **Homework 2** of the **DataTalksClub AI Dev Tools Zoomcamp 2026**, following a disciplined, spec-first AI-native engineering workflow:
1. **Spec First**: Defined product requirements, state transitions, and acceptance criteria in `_docs/specs.md`.
2. **Frontend Prototype**: Built an interactive React Kanban board with a centralized, mockable API abstraction.
3. **OpenAPI Contract**: Designed a formal OpenAPI 3.1 specification (`openapi.yaml`) as the single source of truth.
4. **FastAPI Backend**: Implemented REST endpoints using Pydantic v2 and verified behavior with automated tests.
5. **Database Persistence**: Replaced temporary storage with SQLAlchemy 2.0 and SQLite.

---

## Final Architecture
- **Frontend**: React 19 + Vite 6 responsive board with centralized API client abstraction (`frontend/src/services/api.js`).
- **Backend**: FastAPI with Pydantic v2 input/output validation schemas and CORS middleware.
- **API Contract**: OpenAPI 3.1 specification (`openapi.yaml`) defining all endpoints, query filters, schemas, and error responses.
- **ORM & Database**: SQLAlchemy 2.0 with local SQLite persistence (`tasklane.db`), designed to support relational databases (e.g. PostgreSQL) with minimal application changes.
- **Automated Tests**: Comprehensive `pytest` test suite with `TestClient` covering all CRUD operations, filtering, ordering, validations, and persistence.
- **Package Management**: `uv` for reproducible Python dependency management and `npm` for frontend dependencies.

---

## Core Features
- **Deterministic 5-Stage Workflow**: `Backlog` -> `To Do` -> `In Progress` -> `Review` -> `Done`.
- **Initial Placement**: All new tasks are strictly created in `Backlog`.
- **Sequential Card Controls**: Intuitive previous/next status transitions (`←` / `→`), automatically disabled at column boundaries.
- **Search & Filtering**: Real-time filtering by priority (`low`, `medium`, `high`, `urgent`) and substring search across task titles and descriptions.
- **Deterministic Ordering**: Tasks are returned newest first (`created_at DESC`, `id DESC`).
- **Strict Validation**: Empty titles, invalid priorities, and illegal client-supplied creation statuses are rejected with `422 Unprocessable Entity`.

---

## Project Structure
```text
tasklane/
├── _docs/
│   └── specs.md           # Product specifications and acceptance criteria
├── AGENTS.md              # Agent instructions, commands, and project conventions
├── .gitignore             # Version control exclusion rules (including local SQLite DBs)
├── README.md              # Project documentation and guide
├── openapi.yaml           # OpenAPI 3.1 specification
├── frontend/              # React + Vite application
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx        # Kanban board component
│       ├── App.css        # Board styling and responsive layout
│       ├── index.css      # Design tokens and base styles
│       ├── main.jsx       # Application entrypoint
│       └── services/
│           └── api.js     # Centralized API client abstraction
└── backend/               # FastAPI + SQLAlchemy application
    ├── pyproject.toml     # uv package configuration
    ├── uv.lock            # Reproducible lockfile
    ├── main.py            # FastAPI endpoints, CORS, and lifespan
    ├── database.py        # SQLAlchemy engine, SessionLocal, and get_db dependency
    ├── models.py          # SQLAlchemy Task ORM model
    ├── schemas.py         # Pydantic v2 validation models
    └── tests/
        └── test_api.py    # 11 automated pytest test cases
```

---

## Setup & Running Instructions

### Prerequisites
- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/)
- Node.js 20+ and `npm`

### 1. Backend Setup & Run
From the repository root:
```bash
cd backend
uv run uvicorn main:app --reload
```
The FastAPI backend will start at:
- **API Base URL**: `http://localhost:8000`
- **Interactive OpenAPI Docs**: `http://localhost:8000/docs`

> **Note on Database Persistence**: The local SQLite database (`backend/tasklane.db`) is created automatically on application startup by SQLAlchemy metadata. It is excluded from version control via `.gitignore`.

### 2. Frontend Setup & Run
In a separate terminal, from the repository root:
```bash
cd frontend
npm install
npm run dev
```
The Vite development server will start at:
- **Frontend URL**: `http://localhost:5173`

### 3. Running Automated Tests
To run the full backend test suite:
```bash
cd backend
uv run pytest
```