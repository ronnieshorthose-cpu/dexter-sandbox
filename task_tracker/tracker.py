"""Task model, in-memory tracker, and JSON storage."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass

MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2000


class TaskError(Exception):
    """Raised for invalid task operations (bad input, unknown task ID)."""


class StorageError(Exception):
    """Raised when the task file cannot be read or written safely."""


@dataclass
class Task:
    id: int
    title: str
    description: str = ""
    completed: bool = False

    @classmethod
    def from_dict(cls, data):
        """Build a Task from a dict loaded from JSON, validating every field."""
        if not isinstance(data, dict):
            raise StorageError("each task must be a JSON object")
        task_id = data.get("id")
        title = data.get("title")
        description = data.get("description", "")
        completed = data.get("completed", False)
        # bool is a subclass of int, so reject it explicitly.
        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id < 1:
            raise StorageError(f"invalid task id: {task_id!r}")
        if not isinstance(title, str) or not title.strip():
            raise StorageError(f"task {task_id} has an invalid title")
        if not isinstance(description, str):
            raise StorageError(f"task {task_id} has an invalid description")
        if not isinstance(completed, bool):
            raise StorageError(f"task {task_id} has an invalid completed flag")
        return cls(id=task_id, title=title, description=description, completed=completed)


class TaskTracker:
    """Holds tasks in memory. Persistence is handled by load_tasks/save_tasks."""

    def __init__(self, tasks=None):
        self.tasks = list(tasks or [])
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise StorageError("duplicate task ids found")

    def add(self, title, description=""):
        if not isinstance(title, str) or not title.strip():
            raise TaskError("title must not be empty")
        if not isinstance(description, str):
            raise TaskError("description must be text")
        title = title.strip()
        description = description.strip()
        if len(title) > MAX_TITLE_LENGTH:
            raise TaskError(f"title must be at most {MAX_TITLE_LENGTH} characters")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise TaskError(
                f"description must be at most {MAX_DESCRIPTION_LENGTH} characters"
            )
        next_id = max((task.id for task in self.tasks), default=0) + 1
        task = Task(id=next_id, title=title, description=description)
        self.tasks.append(task)
        return task

    def list(self):
        return list(self.tasks)

    def complete(self, task_id):
        task_id = parse_task_id(task_id)
        for task in self.tasks:
            if task.id == task_id:
                task.completed = True
                return task
        raise TaskError(f"no task with id {task_id}")


def parse_task_id(value):
    """Convert user input to a positive task ID, raising TaskError if invalid."""
    if isinstance(value, bool):
        raise TaskError(f"invalid task id: {value!r}")
    try:
        task_id = int(str(value).strip())
    except ValueError:
        raise TaskError(f"task id must be a whole number, got {value!r}") from None
    if task_id < 1:
        raise TaskError("task id must be 1 or greater")
    return task_id


def load_tasks(path):
    """Load tasks from a JSON file. A missing file means no tasks yet.

    A corrupted or malformed file raises StorageError; the file is left untouched
    so the user can inspect or recover it.
    """
    if not os.path.exists(path):
        return TaskTracker()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise StorageError(f"task file {path} is not valid JSON: {exc}") from None
    except UnicodeDecodeError:
        raise StorageError(f"task file {path} is not valid UTF-8 text") from None
    except OSError as exc:
        raise StorageError(f"could not read task file {path}: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise StorageError(f"task file {path} has an unexpected format")
    return TaskTracker(Task.from_dict(item) for item in data["tasks"])


def save_tasks(path, tracker):
    """Write tasks to a JSON file atomically.

    The data is written to a temporary file in the same directory and then moved
    into place, so an interrupted save cannot leave a half-written task file.
    """
    payload = {"tasks": [asdict(task) for task in tracker.tasks]}
    directory = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tasks-", suffix=".tmp")
    except OSError as exc:
        raise StorageError(f"could not write task file {path}: {exc}") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise StorageError(f"could not write task file {path}: {exc}") from None
