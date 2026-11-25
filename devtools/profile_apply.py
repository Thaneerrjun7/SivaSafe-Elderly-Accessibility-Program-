"""Break the transition cost down into its parts."""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from core import crash_guard, text_scale
crash_guard.install()
import ui

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(ui.SIVASAFE_STYLESHEET)
ui._init_fonts()
text_scale.install(app, ui.SIVASAFE_STYLESHEET)
win = ui.MainWindow("face.png")
win.show()
ts = text_scale.instance()

timings = {}


def wrap(obj, name, key):
    orig = getattr(obj, name)

    def inner(*a, **kw):
        t0 = time.perf_counter()
        try:
            return orig(*a, **kw)
        finally:
            timings[key] = timings.get(key, 0.0) + (time.perf_counter() - t0) * 1000

    setattr(obj, name, inner)


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.002)


def run():
    win._centre_stack.setCurrentIndex(7)
    pump(600)
    surface = win.centralWidget()

    wrap(ts, "apply_all", "apply_all")
    wrap(ts, "_apply_app_stylesheet", "app_stylesheet")
    wrap(win, "_apply_scaled_window_size", "window_resize")
    wrap(win, "_update_responsive_panels", "responsive")

    n_widgets = len(app.allWidgets())
    n_visible = len([w for w in app.allWidgets() if w.isVisible() or w.isWindow()])
    print(f"widgets total {n_widgets}, processed per pass {n_visible}", flush=True)

    wrap(ts, "animate_to", "animate_to_TOTAL")
    wrap(ts, "set_level", "set_level")

    for lvl in (2, 4, 1):
        timings.clear()
        ts.animate_to(lvl, surface)
        pump(600)
        total = timings.get("animate_to_TOTAL", 0.0)
        inner = timings.get("set_level", 0.0)
        print(f"\n  level -> {lvl}   blocking setup before the fade: {total:7.1f} ms")
        for k, v in sorted(timings.items(), key=lambda x: -x[1]):
            print(f"      {k:<18} {v:7.1f} ms")
        print(f"      {'snapshots+layout':<18} {total - inner:7.1f} ms  (2x grab + relayout)")

    ts.set_level(1, persist=False)
    print("\ndone", flush=True)
    app.quit()


QTimer.singleShot(1600, run)
app.exec()
