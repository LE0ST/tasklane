from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskStatus(str, Enum):
    backlog = "backlog"
    todo = "todo"
    in_progress = "in_progress"
    review = "review"
    done = "done"


class TaskPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=120, description="Task title")
    description: str = Field(default="", description="Optional task description")
    priority: TaskPriority = Field(default=TaskPriority.medium, description="Task priority")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Title cannot be empty or contain only whitespace")
        if len(trimmed) > 120:
            raise ValueError("Title cannot exceed 120 characters")
        return trimmed


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None)
    status: Optional[TaskStatus] = Field(default=None)
    priority: Optional[TaskPriority] = Field(default=None)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Title cannot be empty or contain only whitespace")
        if len(trimmed) > 120:
            raise ValueError("Title cannot exceed 120 characters")
        return trimmed


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    created_at: datetime
    updated_at: datetime


class ErrorResponse(BaseModel):
    detail: str