"""
Medication tracker persistence layer — stores medication data in a JSON file.
Location: ~/.siva/health/medications.json
"""

import json
import re
from datetime import datetime
from pathlib import Path
import uuid


# The dose time is a free-text field — the dialog only suggests "e.g. 08:00 AM"
# and takes whatever is typed, so what is actually on disk includes "5pm",
# "8", "17:00" and "8.30 pm". Anything that cannot be read back is a
# medication that never appears in the upcoming list and never announces
# itself, so this is deliberately generous about what it accepts.
_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{1,2}))?(am|pm)?$")
_COMPACT_RE = re.compile(r"^(\d{1,2})(\d{2})(am|pm)?$")


def parse_time_of_day(text: str) -> tuple[int, int] | None:
    """
    Read a typed time of day as (hour, minute) on a 24-hour clock.

    Accepts "08:00 AM", "8am", "5pm", "17:00", "8.30pm", "0800", "noon" and
    "midnight". Returns None for anything it cannot make sense of, which the
    callers treat as "no scheduled time" rather than guessing.
    """
    t = re.sub(r"\s+", "", (text or "").strip().lower())
    # Fold "p.m." / "p.m" / "pm." down to "pm" before anything else looks at
    # the string. Done with a pattern rather than str.replace so a full stop
    # in any of those positions is handled, while the one in "8.30pm" — which
    # is separating the minutes, not decorating the suffix — is left alone.
    t = re.sub(r"([ap])\.?m\.?", r"\1m", t)
    if not t:
        return None
    if t in ("noon", "midday", "12noon"):
        return (12, 0)
    if t == "midnight":
        return (0, 0)

    m = _TIME_RE.match(t)
    if m:
        hour, minute, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    else:
        # "0800" / "1730", written without a separator.
        m = _COMPACT_RE.match(t)
        if not m:
            return None
        hour, minute, suffix = int(m.group(1)), int(m.group(2)), m.group(3)

    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return (hour, minute)


def _store_path() -> Path:
    d = Path.home() / ".siva" / "health"
    d.mkdir(parents=True, exist_ok=True)
    return d / "medications.json"


def load_medications() -> list[dict]:
    """
    Read all medications from the JSON store.
    Handles daily reset of 'taken_today' status.
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
        
        for m in data:
            last_reset = m.get("last_reset_date", "")
            if last_reset != today:
                m["taken_today"] = False
                m["last_reset_date"] = today
                changed = True
                    
        if changed:
            _write_all(data)
            
        return data
    except Exception:
        return []


def _write_all(meds: list[dict]):
    """Write the full medication list back to disk."""
    p = _store_path()
    from core.safe_store import write_json
    write_json(p, meds, indent=2, default=str)


def save_medication(name: str, dosage: str, frequency: str, times: list[str],
                    days_of_week: list[str], duration: str,
                    alarm: bool = False) -> dict:
    """
    Append a new medication to the store and return it.

    `alarm` picks how the reminder announces itself when the time comes: a
    notification (False) or the full alarm window (True).
    """
    meds = load_medications()
    today = datetime.now().strftime("%Y-%m-%d")

    entry = {
        "id": f"SIVAMed_{uuid.uuid4().hex[:12]}",
        "name": name,
        "dosage": dosage,
        "frequency": frequency,
        "times": times,
        "days_of_week": days_of_week,
        "duration": duration,
        "alarm": bool(alarm),
        "taken_today": False,
        "created_at": datetime.now().isoformat(),
        "last_reset_date": today,
    }
    meds.append(entry)
    _write_all(meds)
    return entry


def get_medication(med_id: str) -> dict | None:
    """Look a single medication up by id, or None if it is not stored."""
    for m in load_medications():
        if m.get("id") == med_id:
            return m
    return None


def set_medication_alarm(med_id: str, enabled: bool) -> bool | None:
    """
    Choose between an alarm window and a notification for this medication.

    Like reminders, the scheduled task is left untouched — reminder_fire.py
    reads this flag when it runs, so the change takes effect immediately
    without going back to the OS scheduler.

    Returns the stored state, or **None if no medication has that id**. That
    distinction matters: editing a medication deletes it and re-saves it under
    a fresh id, so a list built before the edit holds ids that no longer exist.
    Reporting that as False would be indistinguishable from "it is now off",
    and the caller would show a switch that quietly refuses to turn on.
    """
    meds = load_medications()
    for m in meds:
        if m.get("id") == med_id:
            m["alarm"] = bool(enabled)
            _write_all(meds)
            return m["alarm"]
    return None


def toggle_medication_taken(med_id: str) -> bool:
    """Flip the `taken_today` flag for a medication. Returns new state."""
    meds = load_medications()
    for m in meds:
        if m["id"] == med_id:
            m["taken_today"] = not m["taken_today"]
            _write_all(meds)
            return m["taken_today"]
    return False


def delete_medication(med_id: str) -> bool:
    """Remove a medication from the store."""
    meds = load_medications()
    before = len(meds)
    meds = [m for m in meds if m["id"] != med_id]
    if len(meds) < before:
        _write_all(meds)
        return True
    return False
