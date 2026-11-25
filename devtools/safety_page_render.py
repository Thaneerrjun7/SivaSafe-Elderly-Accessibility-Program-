r"""
safety_page_render.py — look at the Safety Check page without signing in.

diag_live drives the stack by index, which leaves `_active_nav` on "home" and
so keeps the activity log on screen. This one goes through `_go_to_page`, the
route a real click takes, so what it photographs is what the user sees —
including the log panel getting out of the way.

    ..\.venv\Scripts\python.exe devtools\safety_page_render.py

Writes SAFETY_<level>_<state>.png next to this file.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PyQt6.QtWidgets import QApplication, QAbstractScrollArea   # noqa: E402
from PyQt6.QtCore import QTimer                                 # noqa: E402

from core import crash_guard, prefs                             # noqa: E402
crash_guard.install()
_prev = prefs.get("text_scale_level", 1)
prefs.set("text_scale_level", 1)

import ui                                                        # noqa: E402
from core import text_scale                                      # noqa: E402
from actions.content_safety import check_text, check_image       # noqa: E402

SCAM = ("Maybank: Your account will be suspended within 24 hours. Verify now at "
        "http://maybank2u-verify.top/login and enter your TAC to reactivate.")
CLEAN = ("Maybank: Your monthly statement is ready. Please review it immediately "
         "at https://www.maybank2u.com.my/ if you have any questions.")

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(ui.SIVASAFE_STYLESHEET)
ui._init_fonts()
text_scale.install(app, ui.SIVASAFE_STYLESHEET)
win = ui.MainWindow("face.png")
win.show()

PROBLEMS = []


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def overflow_check(tag):
    for w in app.allWidgets():
        if isinstance(w, QAbstractScrollArea) and w.isVisible():
            if w.horizontalScrollBar().maximum() > 0:
                msg = f"{tag}: {type(w).__name__} overflows by {w.horizontalScrollBar().maximum()}px"
                PROBLEMS.append(msg)
                print("  OVERFLOW  " + msg, flush=True)


def shot(name):
    win.grab().save(os.path.join(OUT, f"SAFETY_{name}.png"))
    print(f"  wrote SAFETY_{name}.png", flush=True)


def png_1024():
    from PIL import Image, PngImagePlugin
    import io
    img = Image.new("RGB", (1024, 1024), (130, 145, 165))
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", "an elderly man smiling, Steps: 30, CFG scale: 7")
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def run():
    pump(700)
    page = win._safety_page

    for level in (1, 4):
        text_scale.instance().set_level(level)
        pump(600)
        win._go_to_page("safety")          # the real click path
        pump(400)

        print(f"\n--- text level {level} "
              f"(log visible: {win._right_panel.isVisible()}) ---")

        page._clear()
        pump(200)
        shot(f"L{level}_empty")
        overflow_check(f"L{level}/empty")

        page._text_input.setPlainText(SCAM)
        page.show_verdict(check_text(SCAM, use_ai=False))
        pump(500)
        shot(f"L{level}_scam")
        overflow_check(f"L{level}/scam")

        page._text_input.setPlainText(CLEAN)
        page.show_verdict(check_text(CLEAN, use_ai=False))
        pump(400)
        shot(f"L{level}_clean")
        overflow_check(f"L{level}/clean")

        page.show_verdict(check_image(png_1024(), use_ai=False))
        pump(400)
        shot(f"L{level}_image")
        overflow_check(f"L{level}/image")

    print("\n" + "=" * 60)
    print("horizontal overflows:", len(PROBLEMS))
    print("RESULT:", "FAIL" if PROBLEMS else "PASS — zero horizontal overflow")
    prefs.set("text_scale_level", _prev)
    app.quit()


QTimer.singleShot(1200, run)
app.exec()
