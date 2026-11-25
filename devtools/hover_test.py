"""
Reproduce the crash: reveal a sidebar AnimatedButton through AnimatedScrollArea,
then hover it. Before the fix this raised
    RuntimeError: wrapped C/C++ object of type QGraphicsDropShadowEffect
                  has been deleted
which PyQt turns into qFatal() -> process abort.

Also hovers every AnimatedButton in the whole window, at every text size.
"""
import os
import sys
import time

ROOT = r"C:\Users\Arjun Shanker\Downloads\Mark-XLVI-main\Mark-XLVI-main"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core import crash_guard
crash_guard.install()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QEvent, QPointF, Qt
from PyQt6.QtGui import QEnterEvent

import ui
from core import text_scale
from core.motion import AnimatedButton, AnimatedScrollArea

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(ui.SIVASAFE_STYLESHEET)
ui._init_fonts()
text_scale.install(app, ui.SIVASAFE_STYLESHEET)
win = ui.MainWindow("face.png")
win.show()

FAILURES = []


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def hover_all(label):
    btns = [w for w in app.allWidgets() if isinstance(w, AnimatedButton)]
    ok = 0
    for b in btns:
        try:
            pos = QPointF(b.rect().center())
            b.enterEvent(QEnterEvent(pos, pos, b.mapToGlobal(b.rect().center()).toPointF()))
            b.leaveEvent(QEvent(QEvent.Type.Leave))
            ok += 1
        except RuntimeError as e:
            FAILURES.append(f"{label}: {b.text()!r} -> {e}")
    print(f"  {label}: hovered {ok}/{len(btns)} AnimatedButtons, "
          f"{len(FAILURES)} failures so far", flush=True)


def run():
    # Force the reveal animation over every scroll area, which is what
    # destroyed the buttons' shadows.
    for sa in [w for w in app.allWidgets() if isinstance(sa_t := w, AnimatedScrollArea)]:
        sa.reset_revealed()
        sa._check_viewport()
    pump(700)

    hover_all("level 1 (default)")

    for lvl in (0, 2, 3, 4):
        text_scale.instance().set_level(lvl, persist=False)
        pump(700)
        for sa in [w for w in app.allWidgets() if isinstance(w, AnimatedScrollArea)]:
            sa.reset_revealed()
            sa._check_viewport()
        pump(700)
        hover_all(f"level {lvl}")

    # Navigate every page and hover again — pages build lazily.
    for i in range(win._centre_stack.count()):
        win._centre_stack.setCurrentIndex(i)
        pump(300)
    hover_all("after visiting every page")

    print()
    if FAILURES:
        print("FAILED:")
        for f in FAILURES:
            print("  ", f)
    else:
        print("PASS — no dangling graphics effects, no aborts")
    text_scale.instance().set_level(1, persist=False)
    app.quit()


QTimer.singleShot(1500, run)
app.exec()
print("EXITED NORMALLY")
