"""
The remote-access card, measured and drawn, at every text size.

    python devtools/remote_card_check.py

Narrow on purpose. The full layout audit covers this card too, but it walks
every page at every size and takes minutes; this opens the one panel and
answers the two questions it was getting wrong — is any of its text cut off,
and does anything scroll sideways — in a couple of seconds. It also writes a
PNG of each size to devtools/, because "no label is short" and "it reads
properly" are not the same claim.

Exit code is 0 when clean, 1 otherwise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from PyQt6.QtWidgets import (  # noqa: E402
    QAbstractScrollArea, QApplication, QLabel, QWidget,
)

from core import text_scale  # noqa: E402
import ui  # noqa: E402

# The canvas the overlay floats in — the app's own minimum window, which is
# the hardest case and the one the fixed-size version failed on.
CANVAS = (1000, 700)
TOL = 2

SAMPLE = (
    "https://192.168.50.242:8001",
    "N3B3BR",
    "https://192.168.50.242:8001/a?k=N3B3BR",
    "192.168.50.242:8001",
)


def main() -> int:
    app = QApplication([])
    # Real font families, so the widths measured here are the widths on the
    # user's screen and the PNGs come out with words in them.
    ui._init_fonts()
    text_scale.install(app, ui.SIVASAFE_STYLESHEET)
    win = ui.MainWindow(face_path="")
    win.show()
    win.resize(*CANVAS)
    win.on_remote_clicked = lambda: SAMPLE

    problems: list[str] = []
    print(f"Remote access card — {CANVAS[0]}x{CANVAS[1]} canvas\n")

    for level in range(len(text_scale.STEPS)):
        text_scale.instance().set_level(level, persist=False)
        text_scale.instance().apply_all(include_hidden=True)
        win.resize(*CANVAS)
        app.processEvents()

        win._open_remote()
        ov = win._remote_overlay
        for _ in range(8):
            app.processEvents()

        label = text_scale.LABELS[level]
        cw = win.centralWidget()
        print(f"  {label:<9} card {ov.width():>4}x{ov.height():<4} "
              f"in {cw.width()}x{cw.height()}")

        for area in ov.findChildren(QAbstractScrollArea):
            rng = area.horizontalScrollBar().maximum()
            if rng > 0:
                problems.append(f"{label}: scrolls sideways +{rng}px")

        if ov.width() > cw.width() + TOL or ov.height() > cw.height() + TOL:
            problems.append(
                f"{label}: card {ov.width()}x{ov.height()} is bigger than "
                f"the window {cw.width()}x{cw.height()}")

        for lbl in ov.findChildren(QLabel):
            if not lbl.isVisible() or not lbl.text() or lbl.width() <= 0:
                continue
            need = (lbl.heightForWidth(lbl.width()) if lbl.wordWrap()
                    else lbl.sizeHint().height())
            if need > lbl.height() + TOL:
                problems.append(
                    f"{label}: {lbl.text()[:34]!r} is {need - lbl.height()}px "
                    f"short of its own text")

        # Overlapping siblings — the original fault, and one that measuring
        # heights alone would not have caught.
        boxes = [(c, c.geometry()) for c in ov._body.findChildren(QLabel)
                 if c.isVisible() and c.parentWidget() is ov._body]
        for i, (a, ra) in enumerate(boxes):
            for b, rb in boxes[i + 1:]:
                hit = ra.intersected(rb)
                if hit.height() > TOL and hit.width() > TOL:
                    problems.append(
                        f"{label}: {a.text()[:20]!r} overlaps "
                        f"{b.text()[:20]!r} by {hit.height()}px")

        shot = _ROOT / "devtools" / f"REMOTE_{label.lower()}.png"
        ov.grab().save(str(shot))

        ov._do_close()
        win._remote_overlay = None
        app.processEvents()

    print()
    if problems:
        print(f"FAILED — {len(problems)} problems")
        for p in problems:
            print(f"  {p}")
        return 1
    print("PASS — nothing cut off, nothing overlapping, no sideways scroll")
    print(f"       wrote REMOTE_*.png to {_ROOT / 'devtools'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
