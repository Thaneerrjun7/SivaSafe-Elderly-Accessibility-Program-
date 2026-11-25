"""
Keep SivaSafe alive.

PyQt aborts the entire process when a Python exception escapes a reimplemented
Qt virtual method — an event handler, a paintEvent, a slot. One dangling
reference in a hover handler is enough to close the app in a user's face.

For an assistant that is meant to sit running on an older person's PC all day,
that trade is wrong: a single broken animation should never take the whole
application down. PyQt honours an installed sys.excepthook in preference to
calling qFatal(), so installing one turns those fatal aborts into logged,
survivable errors.

Exceptions are written to ~/.siva/errors.log so nothing is lost silently.
"""

import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

_MAX_LOG_BYTES = 512 * 1024
_installed = False
_lock = threading.Lock()


def log_path() -> Path:
    d = Path.home() / ".siva"
    d.mkdir(parents=True, exist_ok=True)
    return d / "errors.log"


def _write(text: str) -> None:
    try:
        p = log_path()
        # Cheap rotation so this can never grow without bound on a machine
        # that stays powered on for weeks.
        if p.exists() and p.stat().st_size > _MAX_LOG_BYTES:
            p.replace(p.with_suffix(".log.1"))
        with _lock, open(p, "a", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        pass          # logging must never itself take the app down


def _report(exc_type, exc_value, exc_tb, source: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _write(f"\n=== {stamp}  [{source}] ===\n{body}")
    try:
        sys.stderr.write(f"[SIVA] non-fatal error ({source}) — app kept running:\n{body}")
        sys.stderr.flush()
    except Exception:
        pass


def install() -> None:
    """Call once, as early as possible, before any Qt object is created."""
    global _installed
    if _installed:
        return
    _installed = True

    def _hook(exc_type, exc_value, exc_tb):
        # Ctrl-C should still stop the app.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        _report(exc_type, exc_value, exc_tb, "main")

    sys.excepthook = _hook

    def _thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        _report(args.exc_type, args.exc_value, args.exc_traceback,
                f"thread:{getattr(args.thread, 'name', '?')}")

    threading.excepthook = _thread_hook
