import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app
from models import Task

# Test SQLite in-memory database with StaticPool for thread-safe test isolation
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_database():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


def test_create_task_success():
    """AC-1: Valid task creation always starts in backlog with default priority medium."""
    payload = {"title": "Build landing page", "description": "Quick draft"}
    res = client.post("/api/tasks", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["id"] == 1
    assert data["title"] == "Build landing page"
    assert data["description"] == "Quick draft"
    assert data["status"] == "backlog"
    assert data["priority"] == "medium"
    assert "created_at" in data
    assert "updated_at" in data


def test_create_task_rejects_client_supplied_status():
    """AC-1: Create endpoint strictly rejects any client-supplied status with 422."""
    payload = {"title": "Illegal status task", "status": "in_progress"}
    res = client.post("/api/tasks", json=payload)
    assert res.status_code == 422


def test_create_task_empty_or_whitespace_title_rejected():
    """AC-2: Empty or whitespace-only title returns 422."""
    res1 = client.post("/api/tasks", json={"title": ""})
    assert res1.status_code == 422

    res2 = client.post("/api/tasks", json={"title": "    "})
    assert res2.status_code == 422


def test_create_task_invalid_priority_rejected():
    """AC-3: Invalid priority returns 422."""
    res = client.post("/api/tasks", json={"title": "Invalid task", "priority": "super-urgent"})
    assert res.status_code == 422


def test_list_tasks_deterministic_ordering():
    """AC-4: GET /api/tasks returns tasks ordered newest first."""
    client.post("/api/tasks", json={"title": "Task 1"})
    client.post("/api/tasks", json={"title": "Task 2"})
    client.post("/api/tasks", json={"title": "Task 3"})

    res = client.get("/api/tasks")
    assert res.status_code == 200
    tasks = res.json()
    assert len(tasks) == 3
    assert [t["title"] for t in tasks] == ["Task 3", "Task 2", "Task 1"]


def test_filtering_and_search():
    """AC-5: Filtering by status, priority, and case-insensitive search."""
    client.post("/api/tasks", json={"title": "Frontend UI", "description": "Build with React", "priority": "high"})
    client.post("/api/tasks", json={"title": "Backend API", "description": "Build with FastAPI", "priority": "urgent"})
    client.post("/api/tasks", json={"title": "Database Setup", "description": "Configure SQLite", "priority": "low"})

    # Transition one task to in_progress
    client.patch("/api/tasks/2", json={"status": "in_progress"})

    # Filter by priority
    res_prio = client.get("/api/tasks?priority=urgent")
    assert res_prio.status_code == 200
    assert len(res_prio.json()) == 1
    assert res_prio.json()[0]["title"] == "Backend API"

    # Filter by status
    res_status = client.get("/api/tasks?status=in_progress")
    assert res_status.status_code == 200
    assert len(res_status.json()) == 1
    assert res_status.json()[0]["title"] == "Backend API"

    # Search in title (case-insensitive)
    res_search_title = client.get("/api/tasks?search=FRONTEND")
    assert res_search_title.status_code == 200
    assert len(res_search_title.json()) == 1
    assert res_search_title.json()[0]["title"] == "Frontend UI"

    # Search in description (case-insensitive)
    res_search_desc = client.get("/api/tasks?search=sqlite")
    assert res_search_desc.status_code == 200
    assert len(res_search_desc.json()) == 1
    assert res_search_desc.json()[0]["title"] == "Database Setup"


def test_status_progression_lifecycle():
    """AC-6: Status transitions backlog -> todo -> in_progress -> review -> done."""
    res_create = client.post("/api/tasks", json={"title": "Workflow task"})
    task_id = res_create.json()["id"]

    for next_status in ["todo", "in_progress", "review", "done"]:
        res_patch = client.patch(f"/api/tasks/{task_id}", json={"status": next_status})
        assert res_patch.status_code == 200
        assert res_patch.json()["status"] == next_status


def test_patch_validation_errors():
    """AC-3: Invalid values on PATCH return 422."""
    client.post("/api/tasks", json={"title": "Task"})

    # Invalid status
    res1 = client.patch("/api/tasks/1", json={"status": "archived"})
    assert res1.status_code == 422

    # Invalid priority
    res2 = client.patch("/api/tasks/1", json={"priority": "critical"})
    assert res2.status_code == 422

    # Empty title
    res3 = client.patch("/api/tasks/1", json={"title": "   "})
    assert res3.status_code == 422


def test_delete_task():
    """AC-7: DELETE /api/tasks/{id} returns 204, subsequent lookup returns 404."""
    client.post("/api/tasks", json={"title": "Task to delete"})
    res_del = client.delete("/api/tasks/1")
    assert res_del.status_code == 204

    res_get = client.get("/api/tasks/1")
    assert res_get.status_code == 404


def test_non_existent_resource_returns_404():
    """AC-8: 404 for GET, PATCH, and DELETE on non-existent task IDs."""
    assert client.get("/api/tasks/999").status_code == 404
    assert client.patch("/api/tasks/999", json={"title": "New"}).status_code == 404
    assert client.delete("/api/tasks/999").status_code == 404


def test_database_persistence_across_sessions():
    """AC-9: Verify data is committed to relational storage and queryable via separate DB session."""
    res = client.post(
        "/api/tasks",
        json={"title": "Persisted Task", "description": "Stored in SQLite", "priority": "urgent"},
    )
    assert res.status_code == 201
    created_id = res.json()["id"]

    # Open a completely independent database session
    db = TestingSessionLocal()
    try:
        db_task = db.query(Task).filter(Task.id == created_id).first()
        assert db_task is not None
        assert db_task.title == "Persisted Task"
        assert db_task.description == "Stored in SQLite"
        assert db_task.status == "backlog"
        assert db_task.priority == "urgent"
    finally:
        db.close()