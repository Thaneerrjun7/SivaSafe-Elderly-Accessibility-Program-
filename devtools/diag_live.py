"""
The realistic user path: app starts at whatever size was saved (default here),
then the user drags the slider live to the largest size, THEN visits pages —
never a fresh construction at the target level. This is the path the earlier
boot-at-level-4 harness never exercised, and it's where a construction-time-only
layout decision (computed once in __init__, never revisited) would show up as
a bug the boot harness could not have caught.

Reports horizontalScrollBar().maximum() for every scroll area on every page,
and screenshots each.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PyQt6.QtWidgets import QApplication, QAbstractScrollArea
from PyQt6.QtCore import QTimer

from core import crash_guard, prefs
crash_guard.install()
prev = prefs.get("text_scale_level", 1)
prefs.set("text_scale_level", 1)          # start at DEFAULT, not the target

from actions.medication_store import save_medication, load_medications
if not load_medications():
    save_medication("Clarinase", "1 tablet", "Daily", ["8:00 AM"], ["Everyday"], "30 days")
    save_medication("Metformin", "500mg", "Twice daily", ["8:00 AM", "8:00 PM"], ["Everyday"], "90 days")

import ui
from core import text_scale

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(ui.SIVASAFE_STYLESHEET)
ui._init_fonts()
text_scale.install(app, ui.SIVASAFE_STYLESHEET)
win = ui.MainWindow("face.png")
win.show()

PAGES = {"home": 0, "health": 1, "reminders": 2, "tasks": 4, "chathistory": 6,
         "settings": 7, "safety": 8}
ANY_OVERFLOW = []


def prime_safety():
    """Put a real verdict on the Safety page before measuring it.

    An empty results card proves nothing — the overflow risk on that page lives
    entirely in the wrapped finding rows, which only exist once something has
    been checked. Uses the offline engine so this stays a layout test.
    """
    from actions.content_safety import check_text
    win._safety_page._text_input.setPlainText(
        "Maybank: Your account will be suspended within 24 hours. Verify now at "
        "http://maybank2u-verify.top/login and enter your TAC to reactivate.")
    win._safety_page.show_verdict(check_text(
        "Maybank: Your account will be suspended within 24 hours. Verify now at "
        "http://maybank2u-verify.top/login and enter your TAC to reactivate.",
        use_ai=False))


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def check(tag):
    for w in app.allWidgets():
        if isinstance(w, QAbstractScrollArea) and w.isVisible():
            hbar = w.horizontalScrollBar()
            if hbar.maximum() > 0:
                content = w.widget()
                cw = content.width() if content else -1
                msg = (f"{tag:<24} {type(w).__name__:<20} "
                       f"viewport={w.viewport().width():4d} content={cw:4d} "
                       f"overflow={hbar.maximum()}")
                ANY_OVERFLOW.append(msg)
                print("  OVERFLOW  " + msg, flush=True)


def run():
    pump(600)
    prime_safety()
    pump(200)
    print("--- at DEFAULT (level 1), before any drag ---")
    for name, idx in PAGES.items():
        win._centre_stack.setCurrentIndex(idx)
        pump(200)
        check(f"default/{name}")

    print("\n--- LIVE change to LARGEST (no restart, no fresh construction) ---")
    win._centre_stack.setCurrentIndex(0)
    pump(200)
    text_scale.instance().set_level(4)     # exactly what the slider does
    pump(700)

    for name, idx in PAGES.items():
        win._centre_stack.setCurrentIndex(idx)
        pump(300)
        win.grab().save(os.path.join(OUT, f"LIVE4_{name}.png"))
        check(f"live-4/{name}")

    # Hamburger drawer, opened after the live change.
    win._toggle_hamburger()
    pump(400)
    win.grab().save(os.path.join(OUT, "LIVE4_drawer.png"))
    check("live-4/drawer")
    print("\ndrawer nav button sizes:")
    for key, btn in win._hamburger._drawer_btns.items():
        print(f"  {key:<14} {btn.width()}x{btn.height()}  font={btn.font().pointSizeF():.1f}pt")
    win._toggle_hamburger()
    pump(300)

    print("\nsidebar nav button sizes:")
    for key, btn in win._nav_btns.items():
        print(f"  {key:<10} {btn.width()}x{btn.height()}  font={btn.font().pointSizeF():.1f}pt")

    # Health status medication cards, with real content this time.
    win._centre_stack.setCurrentIndex(1)
    pump(400)
    hp = win._health_page
    for i in range(hp._list_layout.count()):
        card = hp._list_layout.itemAt(i).widget()
        if card:
            print(f"\nmedication card {i}: {card.width()}x{card.height()} "
                  f"sizeHint={card.sizeHint().width()}x{card.sizeHint().height()}")

    print(f"\n{'='*60}")
    print("TOTAL OVERFLOWS FOUND:", len(ANY_OVERFLOW))
    print("RESULT:", "FAIL" if ANY_OVERFLOW else "PASS — zero horizontal overflow anywhere")

    prefs.set("text_scale_level", prev)
    print("\ndone", flush=True)
    app.quit()


QTimer.singleShot(1500, run)
app.exec()
