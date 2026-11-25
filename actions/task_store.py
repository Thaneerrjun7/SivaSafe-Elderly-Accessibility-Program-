"""
Task List persistence layer — stores tasks in a JSON file.
Location: ~/.siva/tasks/tasks.json
"""

import json
from datetime import datetime
from pathlib import Path
import uuid


def _store_path() -> Path:
    d = Path.home() / ".siva" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d / "tasks.json"


def load_tasks() -> list[dict]:
    """
    Read all tasks from the JSON store.
    Handles 'reset everyday' logic for daily tasks.
    """
    p = _store_path()
    if not p.exists():
        return []
        
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
            
        today = datetime.now().strftime("%Y-%m-%d")
        changed = False
        
        for t in data:
            if t.get("is_daily", False):
                last_reset = t.get("last_reset_date", "")
                if last_reset != today:
                    t["done"] = False
                    t["last_reset_date"] = today
                    changed = True
                    
        if changed:
            _write_all(data)
            
        return data
    except Exception:
        return []


def _write_all(tasks: list[dict]):
    """Write the full task list back to disk."""
    p = _store_path()
    from core.safe_store import write_json
    write_json(p, tasks, indent=2, default=str)


def save_task(text: str, is_daily: bool, notes: str = "") -> dict:
    """
    Append a new task to the store and return it.
    """
    tasks = load_tasks()
    today = datetime.now().strftime("%Y-%m-%d")
    
    entry = {
        "id": f"SIVATask_{uuid.uuid4().hex[:12]}",
        "text": text,
        "done": False,
        "is_daily": is_daily,
        "notes": notes,
        "created_at": datetime.now().isoformat(),
        "last_reset_date": today if is_daily else "",
    }
    tasks.append(entry)
    _write_all(tasks)
    return entry


def toggle_task(task_id: str) -> bool:
    """Flip the `done` flag for a task. Returns new done state."""
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t["done"] = not t["done"]
            _write_all(tasks)
            return t["done"]
    return False


def delete_task(task_id: str) -> bool:
    """Remove a task from the store. Returns True if found and removed."""
    tasks = load_tasks()
    before = len(tasks)
    tasks = [t for t in tasks if t["id"] != task_id]
    if len(tasks) < before:
        _write_all(tasks)
        return True
    return False


def update_task_notes(task_id: str, new_notes: str) -> bool:
    """Update the notes for a specific task."""
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t["notes"] = new_notes
            _write_all(tasks)
            return True
    return False
