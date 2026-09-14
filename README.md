# TaskLane: Mini Kanban Board

TaskLane is a lightweight, responsive Mini Kanban Board application designed for streamlined, distraction-free task management. It is built following the AI-native development methodology from **Module 2 of the AI Dev Tools Zoomcamp (2026)**.

## Core Features
- **Deterministic 5-Stage Workflow**: `Backlog` -> `To Do` -> `In Progress` -> `Review` -> `Done`.
- **Sequential Task Progression**: Intuitive previous/next status controls on each task card.
- **Search & Filtering**: Real-time filtering by priority (`low`, `medium`, `high`, `urgent`) and substring search across title and description.
- **Database-Agnostic Persistence**: SQLAlchemy ORM with SQLite for local execution, ready for relational database engines.
- **OpenAPI-First Architecture**: Clear API contract establishing a single source of truth between frontend and backend.

## Project Structure
```text
tasklane/
├── _docs/
│   └── specs.md       # Product specifications and acceptance criteria
├── AGENTS.md          # Agent instructions, commands, and project conventions
├── .gitignore         # Version control exclusion rules
└── README.md          # Project overview and documentation
```

## Upcoming Structure (Implementation Phase)
- `frontend/`: Interactive Kanban UI with centralized API client.
- `backend/`: FastAPI application with Pydantic v2 schemas and SQLAlchemy models.
- `openapi.yaml`: OpenAPI 3.1 specification for client-server communication.
- `tests/`: Automated test suite covering the API and database layers.

## Development Commands (Planned)
### Backend
```bash
cd backend
uv run uvicorn main:app --reload
```

### Frontend
```bash
cd frontend
npm run dev
```

### Running Tests
```bash
cd backend
uv run pytest
```
