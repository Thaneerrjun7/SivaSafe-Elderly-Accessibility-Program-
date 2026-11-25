"""
Getting medications to actually go off.

Medications used to exist only inside SivaSafe: they were listed on the Health
page and counted on the dashboard, but nothing told the person it was time to
take one. Reminders had a scheduled task behind them; medications had nothing.

This gives them one — a recurring Task Scheduler job per medication time, run
through the same silent notifier reminders use, so a medication announces
itself as a notification or as a full alarm depending on its own alarm flag.

Windows-only, and quiet about failure: a medication that cannot be scheduled
must still appear in the app.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime

from actions.reminder import (
    _get_os, _sanitise, _fire_arguments, build_task_xml, delete_task,
    register_task,
)

_DAY_XML = {
    "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday",
    "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday",
    "sunday": "Sunday",
}

_TASK_PREFIX = "SIVAMed_"


def _parse_time(text: str) -> datetime | None:
    """
    Read a stored dose time. The store holds whatever was typed into a free
    text field, so the tolerant parser in medication_store is the one that
    decides what counts as a time — see parse_time_of_day there.
    """
    from actions.medication_store import parse_time_of_day
    parsed = parse_time_of_day(text)
    if parsed is None:
        return None
    return datetime(1900, 1, 1, parsed[0], parsed[1])


def _task_name(med_id: str, when: datetime) -> str:
    # Task names cannot carry the characters an id might pick up later, and a
    # stray one fails the whole schtasks call rather than being escaped.
    safe_id = re.sub(r"[^A-Za-z0-9_]", "", med_id)
    return f"{safe_id}_{when.strftime('%H%M')}"


def _course_end(duration: str) -> datetime | None:
    """
    The date a course of medication finishes, if one was given.

    "Duration / End Date" is another free text field — it holds "Continuous"
    as often as a date. Only an unambiguous ISO date is treated as an end;
    anything else means the medication carries on, which is the safer reading
    of a field nobody filled in properly.
    """
    text = (duration or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _trigger_xml(when: datetime, days_of_week: list[str],
                 end: datetime | None = None) -> str:
    """
    A repeating calendar trigger at this time of day.

    StartBoundary carries today's date: Task Scheduler reads it as the point
    the schedule begins, not as the single time it fires, and rolls forward to
    the next matching occurrence on its own.
    """
    start = datetime.now().replace(hour=when.hour, minute=when.minute,
                                   second=0, microsecond=0)

    named = [_DAY_XML[d.strip().lower()] for d in (days_of_week or [])
             if d.strip().lower() in _DAY_XML]

    if named and len(named) < 7:
        schedule = (
            "<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek>"
            + "".join(f"<{d} />" for d in named)
            + "</DaysOfWeek></ScheduleByWeek>"
        )
    else:
        # "Everyday", an empty list, or all seven named — all mean daily.
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"

    # Let Task Scheduler retire the job itself on the last day of the course,
    # rather than leaving something that has to remember to clean it up.
    end_xml = ""
    if end is not None:
        end_xml = ('<EndBoundary>'
                   f'{end.strftime("%Y-%m-%d")}T23:59:59'
                   '</EndBoundary>')

    return (
        '<CalendarTrigger>'
        f'<StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>'
        f'{end_xml}'
        '<Enabled>true</Enabled>'
        f'{schedule}'
        '</CalendarTrigger>'
    )


def schedule_medication(med: dict) -> int:
    """
    Register one recurring task per time this medication is due.
    Returns how many were registered.
    """
    if _get_os() != "windows" or not med:
        return 0

    med_id = med.get("id", "")
    if not med_id:
        return 0

    # A course that has already finished gets no tasks at all. Announcing a
    # prescription somebody stopped taking three weeks ago is worse than
    # staying quiet, and the store keeps finished medications on the list.
    end = _course_end(med.get("duration", ""))
    if end is not None and end.date() < datetime.now().date():
        return 0

    try:
        from core.win_notify import ensure_registered
        ensure_registered()
    except Exception:
        pass

    name = _sanitise(med.get("name", "Medication"))
    dosage = _sanitise(med.get("dosage", ""))
    message = f"{name} — {dosage}" if dosage else name

    made = 0
    for time_str in med.get("times", []) or []:
        when = _parse_time(time_str)
        if when is None:
            continue
        xml = build_task_xml(
            _trigger_xml(when, med.get("days_of_week", []), end),
            _fire_arguments(med_id, message, "medication"),
            f"SivaSafe Medication — {name}",
        )
        if register_task(_task_name(med_id, when), xml):
            made += 1
    return made


def unschedule_medication(med: dict) -> int:
    """Remove every scheduled task belonging to this medication."""
    if _get_os() != "windows" or not med:
        return 0
    med_id = med.get("id", "")
    removed = 0
    for time_str in med.get("times", []) or []:
        when = _parse_time(time_str)
        if when is not None and delete_task(_task_name(med_id, when)):
            removed += 1
    return removed


def _existing_task_names() -> list[str]:
    """Every medication task Windows currently knows about."""
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "LIST"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return []
        names = []
        for line in result.stdout.splitlines():
            if line.startswith("TaskName:"):
                leaf = line.split(":", 1)[1].strip().lstrip("\\")
                if leaf.startswith(_TASK_PREFIX):
                    names.append(leaf)
        return names
    except Exception:
        return []


def resync_all() -> int:
    """
    Make Windows agree with the medication store.

    Every stored medication is re-registered, and any medication task left
    behind by one that has since been deleted or edited is removed — the app
    edits a medication by deleting it and saving a new one under a fresh id,
    which would otherwise leave the old times firing forever.

    Runs on a background thread at startup. Returns how many tasks were made.
    """
    if _get_os() != "windows":
        return 0

    try:
        from actions.medication_store import load_medications
        meds = load_medications()
    except Exception:
        return 0

    today = datetime.now().date()
    wanted: set[str] = set()
    made = 0
    for med in meds:
        made += schedule_medication(med)

        end = _course_end(med.get("duration", ""))
        if end is not None and end.date() < today:
            # Finished course: schedule_medication made nothing for it, so
            # leaving its old tasks out of `wanted` is what gets them cleared
            # away below rather than left firing.
            continue
        for time_str in med.get("times", []) or []:
            when = _parse_time(time_str)
            if when is not None:
                wanted.add(_task_name(med.get("id", ""), when))

    for orphan in _existing_task_names():
        if orphan not in wanted:
            delete_task(orphan)

    return made
