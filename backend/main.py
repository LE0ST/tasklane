import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Task
from schemas import (
    ErrorResponse,
    TaskCreate,
    TaskPriority,
    TaskResponse,
    TaskStatus,
    TaskUpdate,
)

logger = logging.getLogger(__name__)


def run_migrations():
    """Apply Alembic migrations to head if alembic.ini is present, otherwise create_all.

    If Alembic is configured and fails, the exception is logged and raised to prevent startup on bad schema.
    """
    alembic_ini_path = Path(__file__).resolve().parent / "alembic.ini"
    if alembic_ini_path.exists():
        try:
            from alembic.config import Config
            from alembic import command
            alembic_cfg = Config(str(alembic_ini_path))
            command.upgrade(alembic_cfg, "head")
        except Exception as exc:
            logger.error(f"Database migration failed: {exc}", exc_info=True)
            raise
    else:
        Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database schema is migrated/ready on startup
    run_migrations()
    yield


app = FastAPI(
    title="TaskLane API",
    version="0.1.0",
    description="FastAPI backend for TaskLane Mini Kanban Board with SQLAlchemy",
    lifespan=lifespan,
)

# Enable CORS for development frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get(
    "/api/tasks",
    response_model=List[TaskResponse],
    summary="List tasks with optional filtering and search",
)
def list_tasks(
    status: Optional[TaskStatus] = Query(None, description="Filter by status"),
    priority: Optional[TaskPriority] = Query(None, description="Filter by priority"),
    search: Optional[str] = Query(None, description="Search term"),
    db: Session = Depends(get_db),
):
    query = db.query(Task)
    if status:
        query = query.filter(Task.status == status.value)
    if priority:
        query = query.filter(Task.priority == priority.value)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(Task.title.ilike(term), Task.description.ilike(term))
        )

    # Deterministic ordering: created_at DESC, id DESC
    return query.order_by(Task.created_at.desc(), Task.id.desc()).all()


@app.post(
    "/api/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new task (strictly placed in backlog)",
)
def create_task(task_in: TaskCreate, db: Session = Depends(get_db)):
    db_task = Task(
        title=task_in.title,
        description=task_in.description,
        status=TaskStatus.backlog.value,
        priority=task_in.priority.value,
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@app.get(
    "/api/tasks/{task_id}",
    response_model=TaskResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get task details by ID",
)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return task


@app.patch(
    "/api/tasks/{task_id}",
    response_model=TaskResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Partially update a task",
)
def update_task(task_id: int, task_in: TaskUpdate, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    update_data = task_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if isinstance(value, (TaskStatus, TaskPriority)):
            setattr(task, field, value.value)
        else:
            setattr(task, field, value)

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task


@app.delete(
    "/api/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
    summary="Delete a task by ID",
)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    db.delete(task)
    db.commit()
    return None


@app.get("/health", summary="Health check endpoint", tags=["system"])
@app.get("/api/health", summary="API health check endpoint", tags=["system"])
def health_check():
    """System health check endpoint used by Docker, CI, and deployment platforms."""
    return {"status": "healthy"}


# Mount frontend static assets for production deployment if directory exists
static_dir = Path(__file__).resolve().parent / "static"
if not static_dir.exists():
    static_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")