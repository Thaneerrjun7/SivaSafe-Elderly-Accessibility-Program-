r"""
safety_wiring_test.py — do the ways *in* to the Safety Check page actually work?

The engine has its own test and the layout has its own harness. This covers the
plumbing between them, which is where a feature that works in isolation fails
in front of an examiner: the button that should appear when a file is attached,
the quick action on Home, the jump from the activity-log drop zone, and the
thread-safe entry point the voice tool calls.

    ..\.venv\Scripts\python.exe devtools\safety_wiring_test.py
"""

import io
import os
import sys
import time

ROOT = r"C:\Users\Arjun Shanker\Downloads\Mark-XLVI-main\Mark-XLVI-main"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PyQt6.QtWidgets import QApplication          # noqa: E402
from PyQt6.QtCore import QTimer                   # noqa: E402

from core import crash_guard, prefs               # noqa: E402
crash_guard.install()
_prev = prefs.get("text_scale_level", 1)
prefs.set("text_scale_level", 1)

import ui                                          # noqa: E402
from core import text_scale                        # noqa: E402
from actions.content_safety import check_text      # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(ui.SIVASAFE_STYLESHEET)
ui._init_fonts()
text_scale.install(app, ui.SIVASAFE_STYLESHEET)
win = ui.MainWindow("face.png")
win.show()

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""),
          flush=True)


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for_verdict(seconds: float) -> str:
    """Pump until the card stops saying 'not checked yet'."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        chip = win._safety_page._result_card._chip.text()
        if "NOT CHECKED" not in chip:
            pump(250)                      # let the deferred re-measure land
            return win._safety_page._result_card._chip.text()
        pump(100)
    return win._safety_page._result_card._chip.text()


def make_ai_png(path):
    from PIL import Image, PngImagePlugin
    img = Image.new("RGB", (1024, 1024), (140, 120, 160))
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", "a smiling grandmother, Steps: 28, CFG scale: 7")
    img.save(path, format="PNG", pnginfo=meta)
    return path


def run():
    pump(700)
    tmp = os.path.join(os.environ.get("TEMP", "."), "siva_safety_fixture.png")
    make_ai_png(tmp)

    # 1. The page exists at the index the map claims.
    check("page registered at _PAGE_MAP['safety']",
          win._PAGE_MAP.get("safety") == win._centre_stack.indexOf(win._safety_page),
          f"map={win._PAGE_MAP.get('safety')} actual={win._centre_stack.indexOf(win._safety_page)}")

    # 2. Home quick action reaches it.
    win._go_to_page("home")
    pump(200)
    win._quick_action("LOCAL:safety")
    pump(300)
    check("Home quick action opens the page",
          win._centre_stack.currentWidget() is win._safety_page)

    # 3. Activity log is out of the way there, and back on Home.
    check("activity log hidden on the page", not win._right_panel.isVisible())
    win._go_to_page("home")
    pump(200)
    check("activity log returns on Home", win._right_panel.isVisible())

    # 4. The "Is this safe?" button appears only once a file is attached.
    check("safety button hidden before any file", not win._safety_btn.isVisible())
    win._drop_zone._try_set_file(tmp)
    pump(300)
    check("safety button appears after a file is attached", win._safety_btn.isVisible())
    check("dropped file still reaches the assistant as before",
          win._current_file == tmp,
          f"current_file={os.path.basename(str(win._current_file))}")

    # 5. Clicking it lands on the page with the file loaded and checked.
    #    This one really does call Gemini, so wait on the result rather than
    #    guessing a duration.
    win._on_check_safety_clicked()
    pump(400)
    check("button jumps to the Safety page",
          win._centre_stack.currentWidget() is win._safety_page)
    check("the dropped file was loaded into the page",
          win._safety_page._drop.current_file() == tmp)
    chip = wait_for_verdict(35)
    check("a verdict was rendered for it", "AI" in chip or "SCAM" in chip, f"chip={chip!r}")

    # 6. The page's own extension list takes an .eml; the log's does not.
    check("page accepts .eml", "eml" in win._safety_page._drop._accept)
    check("activity-log zone unchanged", "eml" not in win._drop_zone._accept)

    # 7. The entry point the voice tool uses, called from a worker thread.
    #    SivaUI builds its own MainWindow, so it cannot be instantiated around
    #    this one — the real method is invoked against a stand-in holding the
    #    single attribute it touches, which exercises the actual marshalling
    #    code rather than a copy of it.
    shim = type("_SivaUIShim", (), {"_win": win})()
    verdict = check_text(
        "PDRM: a warrant has been issued. Do not tell anyone. Transfer to this "
        "account immediately to verify your funds.", use_ai=False)
    import threading
    win._safety_page._result_card.clear()          # so the check cannot pass stale
    win._go_to_page("home")
    pump(200)
    threading.Thread(
        target=lambda: ui.SivaUI.show_safety_verdict(shim, verdict),
        daemon=True).start()
    chip = wait_for_verdict(5)
    check("show_safety_verdict renders from another thread", "SCAM" in chip,
          f"chip={chip!r}")
    check("it also navigates to the page",
          win._centre_stack.currentWidget() is win._safety_page)

    # 7b. The blocking screen grab the voice path uses, called from a worker.
    grab = {}
    def _grab():
        try:
            grab["png"] = ui.SivaUI.capture_screen_full(shim)
        except Exception as e:
            grab["err"] = e
    threading.Thread(target=_grab, daemon=True).start()
    deadline = time.time() + 20
    while time.time() < deadline and not grab:
        pump(100)
    check("capture_screen_full returns a PNG to a worker thread",
          isinstance(grab.get("png"), bytes) and grab["png"][:8].startswith(b"\x89PNG"),
          grab.get("err", f"{len(grab.get('png', b''))} bytes"))

    # 8. The disclaimer is present and cannot be cleared away.
    win._safety_page._clear()
    pump(200)
    card = win._safety_page._result_card
    check("disclaimer survives clear()",
          "guide, not a guarantee" in card._disclaimer.text() and card._disclaimer.isVisible())

    try:
        os.remove(tmp)
    except OSError:
        pass

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 66)
    print(f"  {len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        for n in failed:
            print("   FAILED:", n)
    print("=" * 66)
    prefs.set("text_scale_level", _prev)
    app.quit()


def guarded():
    """crash_guard keeps the app alive when a Qt callback raises, which is
    right for the product and wrong for a test — it turns a typo in here into
    a hang with no output. Report and quit instead."""
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        print("\nHARNESS ERROR — the test itself failed, see traceback above.",
              flush=True)
        prefs.set("text_scale_level", _prev)
        app.exit(2)


print("\nSafety Check wiring\n" + "-" * 66)
QTimer.singleShot(1200, guarded)
app.exec()
