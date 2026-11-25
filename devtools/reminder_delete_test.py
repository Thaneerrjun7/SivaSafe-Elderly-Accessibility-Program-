"""
The reminders page can actually remove a reminder.

    python devtools/reminder_delete_test.py

Drives the real buttons on the real page — clicks the bin on a card, then
Clear Passed — against a **faked home directory**, so the store it edits is a
temporary one and `~/.siva` is never touched.

Worth having because the failure it guards against is silent: the store has
had `delete_reminder()` since the beginning and `ui.py` imported it and never
called it, so every reminder ever made stayed until it aged out on its own
twenty hours later. A button that is wired to nothing looks exactly like a
button that works.

Exit code is 0 when clean, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before importing anything that reads Path.home().
_FAKE_HOME = Path(tempfile.mkdtemp(prefix="siva_deltest_"))
os.environ["USERPROFILE"] = str(_FAKE_HOME)
os.environ["HOME"] = str(_FAKE_HOME)

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

_NOW = datetime.now()
_STORE = _FAKE_HOME / ".siva" / "reminders" / "reminders.json"


def _seed() -> None:
    """Two passed, two still to come. Nothing old enough to age out on its
    own — the 20-hour purge would otherwise do the test's work for it."""
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("p1", "took the bins out", _NOW - timedelta(hours=3)),
        ("p2", "morning tablet", _NOW - timedelta(hours=1)),
        ("f1", "call my sister", _NOW + timedelta(hours=2)),
        ("f2", "Viva Presentation", _NOW + timedelta(hours=6)),
    ]
    _STORE.write_text(json.dumps([
        {"id": i, "message": m, "date": d.strftime("%Y-%m-%d"),
         "time": d.strftime("%H:%M"), "done": False,
         "created": _NOW.isoformat()}
        for i, m, d in rows
    ], indent=2), encoding="utf-8")


def _on_disk() -> list[str]:
    if not _STORE.exists():
        return []
    return [r.get("message", "") for r in json.loads(_STORE.read_text("utf-8"))]


def main() -> int:
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from core import text_scale
    import ui

    _seed()
    app = QApplication([])
    ui._init_fonts()
    text_scale.install(app, ui.SIVASAFE_STYLESHEET)

    page = ui.RemindersPage()
    page.resize(1200, 800)
    page.show()
    page.refresh_list()
    app.processEvents()

    problems: list[str] = []
    print(f"store: {_STORE}")
    print(f"seeded: {_on_disk()}\n")

    # ── the bin on one card ──────────────────────────────────────────
    bins = [b for b in page.findChildren(ui.AnimatedButton)
            if b.text() == "🗑"]
    print(f"delete buttons on the page: {len(bins)}")
    if len(bins) != 4:
        problems.append(f"expected a bin on each of the 4 reminders, found {len(bins)}")

    # Deliberately a reminder that has NOT passed, and named rather than taken
    # by position: cards are sorted by time, so bins[0] is whichever happens to
    # sort first. Deleting a future one also separates the two features — a bin
    # that only ever removed passed reminders would pass a laxer test.
    target = "call my sister"
    chosen = [b for b in bins
              if b.accessibleName() == f"Delete reminder: {target}"]
    if not chosen:
        problems.append(f"no delete button found for {target!r}")
    else:
        print(f"clicking the bin on {target!r} (which is still to come)")
        chosen[0].click()
        for _ in range(4):
            app.processEvents()
        left = _on_disk()
        print(f"  store now: {left}")
        expected = ["took the bins out", "morning tablet", "Viva Presentation"]
        if sorted(left) != sorted(expected):
            problems.append(f"after one delete expected {expected}, got {left}")

    # ── Clear Passed ─────────────────────────────────────────────────
    # It asks first, and a modal would hang a headless run — answer Yes the
    # moment it appears. This is also what proves the confirmation exists.
    asked: list[str] = []

    def _auto_answer() -> None:
        for w in app.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                asked.append(w.text())
                w.done(QMessageBox.StandardButton.Yes.value)

    from PyQt6.QtCore import QTimer
    timer = QTimer()
    timer.timeout.connect(_auto_answer)
    timer.start(50)

    print("\nclicking Clear Passed")
    page._clear_btn.click()
    for _ in range(10):
        app.processEvents()
    timer.stop()

    if not asked:
        problems.append("Clear Passed deleted without asking first")
    else:
        print(f"  it asked: {asked[0]!r}")

    left = _on_disk()
    print(f"  store now: {left}")
    for gone in ("took the bins out", "morning tablet"):
        if gone in left:
            problems.append(f"{gone!r} had passed and was not cleared")
    # 'call my sister' was removed by the bin above; the viva is the only
    # future reminder left, and it must survive.
    if "Viva Presentation" not in left:
        problems.append("'Viva Presentation' is still to come and was cleared")

    # ── and again, with nothing left to clear ────────────────────────
    asked.clear()
    timer.start(50)
    page._clear_btn.click()
    for _ in range(10):
        app.processEvents()
    timer.stop()
    if not asked:
        problems.append("Clear Passed said nothing when there was nothing to clear")
    elif "Nothing to clear" not in asked[0]:
        problems.append(f"expected a 'nothing to clear' message, got {asked[0]!r}")
    else:
        print(f"\nwith nothing passed, it says: {asked[0]!r}")
    if _on_disk() != ["Viva Presentation"]:
        problems.append(
            f"the second Clear Passed changed the store: {_on_disk()}")

    print()
    if problems:
        print(f"FAILED — {len(problems)} problems")
        for p in problems:
            print(f"  {p}")
        return 1
    print("PASS — the bin removes one, Clear Passed removes the past and "
          "keeps the future, and both ask or report as intended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
