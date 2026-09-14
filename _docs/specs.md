# Product Specification: TaskLane

## 1. Overview & Vision
**TaskLane** is a lightweight, responsive Mini Kanban Board application designed for simple, distraction-free workflow management. It strictly adheres to an AI-native full-stack architecture, featuring a clean frontend, a formal OpenAPI specification, and a database-agnostic FastAPI backend backed by SQLAlchemy and SQLite.

The project is intentionally scoped to avoid over-engineering while providing a complete, testable full-stack implementation for Homework 2 of the AI Dev Tools Zoomcamp.

---

## 2. Core Functional Scope

### 2.1 Base Workflow Columns
Tasks move sequentially across 5 core status lanes:
```
Backlog -> To Do -> In Progress -> Review -> Done
```

### 2.2 Task Lifecycle & State Transitions
- **Creation:** Users can create tasks with a mandatory title, optional description, and optional priority.
- **Initial Status:** All newly created tasks always start in `backlog`. Status cannot be set during creation; transitions occur exclusively through `PATCH /api/tasks/{id}`.
- **Controls (No Drag-and-Drop):** Each card provides deterministic status navigation:
  - **Move Backward (`<-`):** Transitions the task to the immediate predecessor state (disabled if currently in `backlog`).
  - **Move Forward (`->`):** Transitions the task to the immediate successor state (disabled if currently in `done`).
- **Deletion:** Tasks can be deleted permanently from any column.

### 2.3 Search, Filtering & Deterministic Ordering
- **Text Search:** Filter tasks by substring search across `title` and `description` (case-insensitive).
- **Priority Filter:** Filter board columns by priority (`low`, `medium`, `high`, `urgent`).
- **Status Filter:** Optional query filter by specific column status.
- **Deterministic Ordering:** Tasks are sorted newest first by `created_at DESC`, breaking ties with `id DESC`.

---

## 3. Data Model

### Entity: `Task`
| Field | Type | Constraints / Allowed Values | Description |
|---|---|---|---|
| `id` | Integer | Primary Key, Auto-increment | Unique identifier |
| `title` | String(120) | Not Null, Stripped Length 1-120 | Concise summary of the task |
| `description` | Text | Nullable, Default `""` | Optional details or notes |
| `status` | String(20) | Enum: `backlog`, `todo`, `in_progress`, `review`, `done` | Current Kanban lane (Default: `backlog`) |
| `priority` | String(20) | Enum: `low`, `medium`, `high`, `urgent` | Priority rating (Default: `medium`) |
| `created_at` | DateTime (UTC) | Not Null, Auto-generated on insert | Timestamp when task was created |
| `updated_at` | DateTime (UTC) | Not Null, Auto-generated on update | Timestamp when task was last modified |

---

## 4. Validation Rules

1. **Title Validation:**
   - Must not be empty, null, or contain only whitespace characters.
   - Stripped length must be between 1 and 120 characters.
   - Violations return `422 Unprocessable Entity`.
2. **Status Validation:**
   - Must match one of: `['backlog', 'todo', 'in_progress', 'review', 'done']`.
   - Status updates are allowed only via `PATCH /api/tasks/{id}`.
   - Any other value returns `422 Unprocessable Entity`.
3. **Priority Validation:**
   - Must match one of: `['low', 'medium', 'high', 'urgent']`.
   - Any other value returns `422 Unprocessable Entity`.
4. **Partial Updates (PATCH):**
   - Allows updating one or more fields (`title`, `description`, `status`, `priority`).
   - Any provided field must adhere to the corresponding validation rules.

---

## 5. API Endpoints

All endpoints are rooted at `/api/tasks`.

| Method | Endpoint | Query Parameters | Request Body | Success Response | Error Responses |
|---|---|---|---|---|---|
| **GET** | `/api/tasks` | `status` (optional)<br>`priority` (optional)<br>`search` (optional) | None | `200 OK`<br>`List[TaskResponse]` (sorted newest first) | `422 Unprocessable Entity` (if invalid status/priority filter) |
| **POST** | `/api/tasks` | None | `{ "title": str, "description"?: str, "priority"?: str }` | `201 Created`<br>`TaskResponse` (always starts in `backlog`) | `422 Unprocessable Entity` |
| **GET** | `/api/tasks/{id}` | None | None | `200 OK`<br>`TaskResponse` | `404 Not Found`<br>`422 Unprocessable Entity` (non-integer id) |
| **PATCH** | `/api/tasks/{id}` | None | `{ "title"?: str, "description"?: str, "priority"?: str, "status"?: str }` | `200 OK`<br>`TaskResponse` | `404 Not Found`<br>`422 Unprocessable Entity` |
| **DELETE** | `/api/tasks/{id}` | None | None | `204 No Content` | `404 Not Found`<br>`422 Unprocessable Entity` |

---

## 6. Acceptance Criteria

- [ ] **AC-1 (Creation):** Submitting a valid title creates a task strictly in the `backlog` column with default priority `medium` and returns `201 Created`. The create endpoint rejects any client-supplied `status` with `422 Unprocessable Entity`.
- [ ] **AC-2 (Validation - Empty Title):** Submitting an empty title or title containing only spaces returns `422 Unprocessable Entity` and does not persist data.
- [ ] **AC-3 (Validation - Invalid Fields):** Providing an invalid `priority` or invalid `status` (on patch) returns `422 Unprocessable Entity`.
- [ ] **AC-4 (Deterministic Ordering):** `GET /api/tasks` returns tasks ordered newest first (`created_at DESC`, `id DESC`).
- [ ] **AC-5 (Filtering & Search):** Passing `?search=keyword` matches tasks containing `keyword` in either title or description. Passing `?priority=high` filters only high priority tasks.
- [ ] **AC-6 (Status Progression & Regression):** Updating status follows the sequential order `backlog` -> `todo` -> `in_progress` -> `review` -> `done` via `PATCH /api/tasks/{id}`.
- [ ] **AC-7 (Deletion):** `DELETE /api/tasks/{id}` removes the task and returns `204 No Content`. Subsequent lookups return `404 Not Found`.
- [ ] **AC-8 (Non-Existent Resources):** Requests to `GET`, `PATCH`, or `DELETE` on a non-existent task id return `404 Not Found`.
- [ ] **AC-9 (Database Agnosticism):** The persistence layer uses SQLAlchemy ORM with SQLite and is designed to support migration to other SQLAlchemy-supported relational databases with minimal application changes.
- [ ] **AC-10 (Test Suite):** Comprehensive automated test suite (`pytest`) verifies all CRUD operations, filtering, search, ordering, validation errors, and 404 conditions.

---

## 7. Architecture & Stack Guidelines

- **Frontend:** Modular web client with a centralized API client abstraction (`api.js` / `api.ts`). Initially supports mock responses, then seamlessly connects to the real FastAPI endpoint.
- **Backend:** FastAPI with Pydantic v2 schemas for strict input/output validation.
- **ORM / Database:** SQLAlchemy 2.0 with SQLite database engine.
- **Package Management:** `uv` for reproducible dependency management.
- **Language & Conventions:** 100% English across the entire project (source code, identifiers, docstrings, UI, tests, docs, and git commit history).
