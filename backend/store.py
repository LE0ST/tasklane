from datetime import datetime, timezone
from typing import Dict, List, Optional
from schemas import TaskCreate, TaskPriority, TaskResponse, TaskStatus, TaskUpdate


class MockTaskStore:
    """In-memory mock database store for Question 5 stage."""

    def __init__(self):
        self._tasks: Dict[int, dict] = {}
        self._next_id: int = 1

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        search: Optional[str] = None
    ) -> List[dict]:
        tasks = list(self._tasks.values())

        if status:
            tasks = [t for t in tasks if t["status"] == status]
        if priority:
            tasks = [t for t in tasks if t["priority"] == priority]
        if search and search.strip():
            query = search.strip().lower()
            tasks = [
                t for t in tasks
                if query in t["title"].lower() or query in (t["description"] or "").lower()
            ]

        # Deterministic ordering: created_at DESC, id DESC
        tasks.sort(key=lambda t: (t["created_at"], t["id"]), reverse=True)
        return tasks

    def get_task(self, task_id: int) -> Optional[dict]:
        return self._tasks.get(task_id)

    def create_task(self, task_in: TaskCreate) -> dict:
        task_id = self._next_id
        self._next_id += 1
        now = datetime.now(timezone.utc)

        task_record = {
            "id": task_id,
            "title": task_in.title,
            "description": task_in.description,
            "status": TaskStatus.backlog,  # Strictly starts in backlog
            "priority": task_in.priority,
            "created_at": now,
            "updated_at": now,
        }
        self._tasks[task_id] = task_record
        return task_record

    def update_task(self, task_id: int, task_in: TaskUpdate) -> Optional[dict]:
        task_record = self._tasks.get(task_id)
        if not task_record:
            return None

        update_data = task_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            task_record[field] = value

        task_record["updated_at"] = datetime.now(timezone.utc)
        return task_record

    def delete_task(self, task_id: int) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False

    def reset(self):
        self._tasks.clear()
        self._next_id = 1


store = MockTaskStore()