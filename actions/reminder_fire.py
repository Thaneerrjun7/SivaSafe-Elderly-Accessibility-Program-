"""
What Windows runs when a reminder comes due.

Task Scheduler launches this with pythonw.exe — a windowless interpreter — so
a reminder arriving is just the notification appearing. Nothing else is drawn
on screen. It replaced a per-reminder PowerShell script, which could not avoid
flashing a console window for an instant on its way to showing a toast.

It is handed the reminder's id and message on the command line, then decides
how to announce it by reading the alarm flag out of the store at the moment it
runs. That is what lets the alarm toggle in the app be instant: switching a
reminder between a notification and an alarm changes a JSON field, and the
scheduled task behind it never has to be touched.

    pythonw.exe reminder_fire.py --kind reminder --id <id> --message "..."
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _log(line: str):
    """
    pythonw.exe has no console, so anything printed here goes nowhere. A
    reminder that silently fails to fire is the worst possible bug in this
    feature, so every run leaves a trace on disk instead.
    """
    try:
        log = Path.home() / ".siva" / "reminders" / "fire.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {line}\n")
    except Exception:
        pass


def _wants_alarm(kind: str, item_id: str) -> bool:
    """
    Read the alarm flag straight from the JSON store.

    Deliberately a plain file read rather than an import of the store modules:
    this runs in its own process with no application around it, and the fewer
    things that have to import cleanly before a reminder can go off, the
    better. A missing or unreadable store means a notification, which is the
    quieter of the two failure modes.
    """
    if not item_id:
        return False
    try:
        if kind == "medication":
            path = Path.home() / ".siva" / "health" / "medications.json"
        else:
            path = Path.home() / ".siva" / "reminders" / "reminders.json"
        if not path.exists():
            return False
        for entry in json.loads(path.read_text(encoding="utf-8")):
            if entry.get("id") == item_id:
                return bool(entry.get("alarm", False))
    except Exception as e:
        _log(f"could not read the alarm flag for {item_id}: {e}")
    return False


def _subtitle(kind: str) -> str:
    return "Medication" if kind == "medication" else "Reminder"


def fire(kind: str, item_id: str, message: str, force_alarm: bool = False) -> int:
    alarm = force_alarm or _wants_alarm(kind, item_id)
    _log(f"firing {kind} {item_id!r} as {'alarm' if alarm else 'notification'}")

    if alarm:
        from actions.alarm_window import show_alarm
        return show_alarm(message, _subtitle(kind), item_id, kind)

    from core.win_notify import show_toast
    title = "💊 Medication" if kind == "medication" else "⏰ Reminder"
    # This process exists only to show the toast and stop, so it has to wait
    # for Windows to take delivery before it does. See show_toast's docstring.
    if not show_toast(title, message, linger=3.0):
        # The toast is the whole point of the run; if Windows would not show
        # it, fall back to the alarm rather than let the reminder evaporate.
        _log("toast refused — falling back to the alarm window")
        from actions.alarm_window import show_alarm
        return show_alarm(message, _subtitle(kind), item_id, kind)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fire a due SivaSafe reminder")
    ap.add_argument("--kind", default="reminder",
                    choices=("reminder", "medication"))
    ap.add_argument("--id", dest="item_id", default="")
    ap.add_argument("--message", default="Reminder")
    ap.add_argument("--alarm", action="store_true",
                    help="ring the alarm regardless of what the store says")
    args = ap.parse_args()

    try:
        return fire(args.kind, args.item_id, args.message, args.alarm)
    except Exception:
        _log("FAILED:\n" + traceback.format_exc())
        # Last resort: a plain beep is better than a reminder that passes in
        # complete silence because something in the app failed to import.
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
