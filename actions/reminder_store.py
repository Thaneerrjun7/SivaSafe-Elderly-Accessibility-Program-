"""
Reminder persistence layer — stores all reminders in a JSON file.
Location: ~/.siva/reminders/reminders.json
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


def _store_path() -> Path:
    d = Path.home() / ".siva" / "reminders"
    d.mkdir(parents=True, exist_ok=True)
    return d / "reminders.json"


def load_reminders() -> list[dict]:
    """Read all reminders from the JSON store. Auto-delete if > 20 hours overdue."""
    p = _store_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []

        from datetime import datetime, timedelta
        now = datetime.now()
        filtered = []
        changed = False

        for r in data:
            date_str = r.get("date", "")
            time_str = r.get("time", "")
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                if now - dt > timedelta(hours=20):
                    changed = True
                    continue  # Drop this reminder
            except Exception:
                pass
            filtered.append(r)

        if changed:
            from core.safe_store import write_json
            write_json(p, filtered, indent=2, default=str)

        return filtered
    except Exception:
        return []


def _write_all(reminders: list[dict]):
    """Write the full reminder list back to disk."""
    p = _store_path()
    from core.safe_store import write_json
    write_json(p, reminders, indent=2, default=str)


def save_reminder(
    message: str,
    date_str: str,
    time_str: str,
    task_name: str = "",
    reminder_id: str | None = None,
    alarm: bool = False,
) -> dict:
    """
    Append a new reminder to the store and return it.

    Parameters
    ----------
    message   : Reminder text
    date_str  : "YYYY-MM-DD"
    time_str  : "HH:MM"
    task_name : OS‑scheduler task name (optional)
    reminder_id : Custom ID; auto‑generated if omitted
    alarm     : True rings a full alarm window instead of a notification
    """
    reminders = load_reminders()
    entry = {
        "id": reminder_id or f"SIVAReminder_{uuid.uuid4().hex[:12]}",
        "message": message,
        "date": date_str,
        "time": time_str,
        "task_name": task_name,
        "alarm": bool(alarm),
        "created_at": datetime.now().isoformat(),
    }
    reminders.append(entry)
    _write_all(reminders)
    return entry


def get_reminder(reminder_id: str) -> dict | None:
    """Look a single reminder up by id, or None if it is no longer stored."""
    for r in load_reminders():
        if r.get("id") == reminder_id:
            return r
    return None


def set_reminder_alarm(reminder_id: str, enabled: bool) -> bool | None:
    """
    Choose how a reminder announces itself: an alarm window (True) or a
    notification (False). Returns the state that was stored, or **None if no
    reminder has that id** — a list built before the reminder was deleted or
    aged out still holds it, and reporting that as False would look exactly
    like "it is now off" to the caller.

    The scheduled task is deliberately left alone. It fires reminder_fire.py,
    which reads this flag at the moment it runs, so switching a reminder
    between alarm and notification never has to touch the OS scheduler.
    """
    reminders = load_reminders()
    for r in reminders:
        if r.get("id") == reminder_id:
            r["alarm"] = bool(enabled)
            _write_all(reminders)
            return r["alarm"]
    return None


def snooze_reminder(reminder_id: str, minutes: int = 5) -> bool:
    """
    Push a reminder forward, so the app shows the time it will next ring at
    rather than the time it originally went off.

    The alarm window does its own waiting — this is the record of it, not the
    mechanism. It also resets the clock on load_reminders()' 20-hour cleanup,
    which would otherwise start counting from the original time.
    """
    from datetime import timedelta
    target = datetime.now() + timedelta(minutes=minutes)
    reminders = load_reminders()
    for r in reminders:
        if r.get("id") == reminder_id:
            r["date"] = target.strftime("%Y-%m-%d")
            r["time"] = target.strftime("%H:%M")
            r["snoozed"] = True
            r["done"] = False
            _write_all(reminders)
            return True
    return False


def mark_reminder_fired(reminder_id: str) -> bool:
    """Mark a reminder answered — dismissed from the alarm window."""
    reminders = load_reminders()
    for r in reminders:
        if r.get("id") == reminder_id:
            r["done"] = True
            r["snoozed"] = False
            _write_all(reminders)
            return True
    return False


def toggle_reminder(reminder_id: str) -> bool:
    """
    Flip the `done` flag for a reminder. Returns the new done state.

    `.get("done", False)` rather than `r["done"]` because reminders written by
    `save_reminder` have no `done` key at all — every entry already on disk
    predates this, and a KeyError here takes the whole reminders panel down.
    """
    reminders = load_reminders()
    for r in reminders:
        if r.get("id") == reminder_id:
            r["done"] = not r.get("done", False)
            _write_all(reminders)
            return r["done"]
    return False


def delete_reminder(reminder_id: str) -> bool:
    """Remove a reminder from the store. Returns True if found and removed."""
    reminders = load_reminders()
    before = len(reminders)
    reminders = [r for r in reminders if r["id"] != reminder_id]
    if len(reminders) < before:
        _write_all(reminders)
        return True
    return False


def get_active_reminders() -> list[dict]:
    """Return all reminders (auto-deleted if > 20h old), sorted by date+time ascending."""
    active = load_reminders()
    try:
        active.sort(key=lambda r: f"{r.get('date', '')} {r.get('time', '')}")
    except Exception:
        pass
    return active


def get_all_sorted() -> list[dict]:
    """Return all reminders sorted: active first (by datetime), then done."""
    reminders = load_reminders()
    active = [r for r in reminders if not r.get("done", False)]
    done = [r for r in reminders if r.get("done", False)]
    try:
        active.sort(key=lambda r: f"{r.get('date', '')} {r.get('time', '')}")
        done.sort(key=lambda r: f"{r.get('date', '')} {r.get('time', '')}")
    except Exception:
        pass
    return active + done
