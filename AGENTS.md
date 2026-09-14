# TaskLane - Agent Instructions & Context

## Project Overview
TaskLane is a Mini Kanban Board application built for Module 2 of the AI Dev Tools Zoomcamp. It implements a sequential 5-stage task workflow (`backlog` -> `todo` -> `in_progress` -> `review` -> `done`).

## Primary Specification
Always consult and adhere to `_docs/specs.md` before making design or architectural decisions. Do not add features outside the specified scope.

## Planned Commands
- **Backend Run:** `cd backend && uv run uvicorn main:app --reload`
- **Backend Tests:** `cd backend && uv run pytest`
- **Frontend Run:** `cd frontend && npm run dev`

## Core Rules & Constraints
1. **Language:** Everything in this project must be strictly in **English** (code, variables, comments, docstrings, UI copy, commit messages, and documentation).
2. **Scope Discipline:** Keep the implementation lean and avoid over-engineering. No drag-and-drop libraries; use sequential next/previous buttons.
3. **Creation Rule:** New tasks always start in `backlog`. The `POST /api/tasks` endpoint must not allow or require setting a `status`. Status transitions occur exclusively through `PATCH /api/tasks/{id}`.
4. **API Contract:** All communication between frontend and backend must adhere to the OpenAPI specification.
5. **Persistence:** Use SQLAlchemy 2.0 with SQLite (`tasklane.db`), keeping queries and models standard and database-agnostic.
6. **Testing:** All endpoints and edge cases (creation, listing, search/filter, update, delete, 422 validations, 404s) must be covered with automated tests.
