from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from schemas import (
    ErrorResponse,
    TaskCreate,
    TaskPriority,
    TaskResponse,
    TaskStatus,
    TaskUpdate,
)
from store import store

app = FastAPI(
    title="TaskLane API",
    version="0.1.0",
    description="FastAPI backend for TaskLane Mini Kanban Board",
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
):
    return store.list_tasks(status=status, priority=priority, search=search)


@app.post(
    "/api/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new task (strictly placed in backlog)",
)
def create_task(task_in: TaskCreate):
    return store.create_task(task_in)


@app.get(
    "/api/tasks/{task_id}",
    response_model=TaskResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get task details by ID",
)
def get_task(task_id: int):
    task = store.get_task(task_id)
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
def update_task(task_id: int, task_in: TaskUpdate):
    updated = store.update_task(task_id, task_in)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return updated


@app.delete(
    "/api/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
    summary="Delete a task by ID",
)
def delete_task(task_id: int):
    success = store.delete_task(task_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return None