"""
The permission gates are actually wired to the permission store.

    python devtools/permission_test.py

Runs against a **faked config directory**, so the real
`config/preferences.json` is never read or written.

Worth having because the failure it guards against is silent, and this project
has already had it once: `delete_reminder()` sat in the store for months while
`ui.py` imported it and never called it, and a bin wired to nothing looks
exactly like a bin that works. A permission gate is the same shape of trap —
one that consults nothing allows everything, and looks perfect from the outside
because every feature still works.

So this drives the real buttons on the real window and checks the store on disk
afterwards, rather than testing `core/permissions.py` on its own.

Five things:

  1. the store round-trips, and a damaged file reads as *allowed* — the one
     place in this app where a corrupt file must not fail closed
  2. every tool `main.py` declares resolves through `for_tool`, so a typo
     cannot silently leave one ungated
  3. a first press really is blocked until the card is answered, and Allow
     really does let the same press through
  4. Allow is remembered and Cancel is not — the asymmetry the whole design
     rests on
  5. a request made from a worker thread reaches the GUI thread and comes back
     with an answer, which is the §11 trap that made the safety card silently
     never appear

Exit code is 0 when clean, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

# Redirect the preference file before anything reads it. Every call goes through
# prefs._base_dir() at the point of use, so patching it here is enough — the
# same trick the UI render harness uses on Path.home.
_FAKE = Path(tempfile.mkdtemp(prefix="siva_permtest_"))
from core import prefs                                    # noqa: E402
prefs._base_dir = lambda: _FAKE                            # type: ignore[assignment]

from core import permissions                               # noqa: E402

_PREF_FILE = _FAKE / "config" / "preferences.json"

problems: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        problems.append(msg)


# ── 1. the store ─────────────────────────────────────────────────────

def test_store() -> None:
    print("\nstore")
    permissions.reset_all()
    check(permissions.granted_keys() == set(), "starts with nothing granted")
    check(not permissions.is_granted(permissions.CAMERA),
          "camera is not granted before it is asked for")

    permissions.grant(permissions.CAMERA)
    check(permissions.is_granted(permissions.CAMERA), "grant sticks")
    check(not permissions.is_granted(permissions.WHATSAPP),
          "granting one does not grant another")

    prefs.set("text_scale_level", 3)          # a neighbour to protect
    permissions.grant(permissions.CAMERA)
    raw = json.loads(_PREF_FILE.read_text("utf-8"))
    check(raw.get(permissions._STORE_KEY) == [permissions.CAMERA],
          "granting twice does not write a duplicate")
    check(raw.get("text_scale_level") == 3,
          "writing a permission leaves the other preferences alone")
    check(list(raw).count(permissions._STORE_KEY) == 1,
          "all of it lives under one key — nothing accumulates")

    permissions.revoke(permissions.CAMERA)
    check(not permissions.is_granted(permissions.CAMERA), "revoke sticks")

    for key in permissions.ORDER:
        permissions.grant(key)
    check(len(permissions.granted_keys()) == len(permissions.ORDER),
          f"all {len(permissions.ORDER)} can be granted")
    permissions.reset_all()
    check(permissions.granted_keys() == set(), "reset_all clears every one")

    # An unknown key is not a question anybody can answer, so it must not block.
    check(permissions.is_granted("no_such_permission"),
          "an unknown key is treated as needing no permission")

    # The one place a damaged file must NOT fail closed: a truncated write
    # should cost one re-ask at worst, never an assistant that refuses
    # everything with no way to explain itself.
    _PREF_FILE.write_text("{ this is not json", encoding="utf-8")
    check(permissions.granted_keys() == set(),
          "a corrupt preferences file reads as nothing granted, not a crash")
    _PREF_FILE.write_text(json.dumps({permissions._STORE_KEY: "not a list"}),
                          encoding="utf-8")
    check(permissions.granted_keys() == set(),
          "a wrong-typed value reads as nothing granted, not a crash")
    permissions.reset_all()


# ── 2. every declared tool ───────────────────────────────────────────

def test_tool_map() -> None:
    print("\ntool mapping")
    import main

    names = [t["name"] for t in main.TOOL_DECLARATIONS]
    check(len(names) == 20, f"main.py declares 20 tools (found {len(names)})")

    # Named here rather than derived, so adding a tool to main.py without
    # deciding what it is allowed to do fails this test instead of defaulting
    # to ungated.
    expected = {
        "open_app":          permissions.APPS,
        "web_search":        permissions.WEB,
        "weather_report":    permissions.LOCATION,
        "send_message":      permissions.WHATSAPP,
        "reminder":          permissions.REMINDERS,
        "youtube_video":     permissions.WEB,
        "screen_process":    permissions.SCREEN,
        "computer_settings": permissions.SYSTEM,
        "browser_control":   permissions.WEB,
        "file_controller":   permissions.FILES,
        "desktop_control":   permissions.AUTOMATION,
        "computer_control":  permissions.SYSTEM,
        "flight_finder":     permissions.WEB,
        "shutdown_siva":     None,
        "file_processor":    permissions.FILES,
        "save_memory":       permissions.MEMORY,
        "plain_reader":      permissions.FILES,
        "gmail_navigator":   permissions.EMAIL,
        "find_location":     permissions.LOCATION,
        "content_safety":    permissions.SCREEN,
    }
    missing = [n for n in names if n not in expected]
    check(not missing, f"no declared tool is unaccounted for {missing or ''}")

    wrong = []
    for name in names:
        got = permissions.for_tool(name, {})
        if got != expected.get(name):
            wrong.append(f"{name}: {got!r} != {expected.get(name)!r}")
    check(not wrong, f"each tool maps where it should {wrong or ''}")

    # The argument-dependent ones. WhatsApp having its own permission is the
    # whole reason these two cannot be answered from the tool name alone.
    check(permissions.for_tool("open_app", {"app_name": "WhatsApp"})
          == permissions.WHATSAPP, "open_app WhatsApp asks about WhatsApp")
    check(permissions.for_tool("open_app", {"app_name": "notepad"})
          == permissions.APPS, "open_app notepad asks about programs")
    check(permissions.for_tool("send_message", {"platform": "wp"})
          == permissions.WHATSAPP, "send_message wp asks about WhatsApp")
    check(permissions.for_tool("send_message", {"platform": "telegram"})
          == permissions.APPS, "send_message telegram asks about programs")
    check(permissions.for_tool("content_safety", {"text": "hello"}) is None,
          "checking pasted text needs no permission")
    check(permissions.for_tool("content_safety", {"file_path": "a.eml"})
          == permissions.FILES, "checking a file asks about files")
    check(permissions.for_tool("shutdown_siva", {}) is None,
          "the way out is never gated")

    # Every permission carries the wording the card needs.
    bad = [k for k in permissions.ORDER
           if not (permissions.describe(k).title.endswith("?")
                   and permissions.describe(k).body
                   and permissions.describe(k).name)]
    check(not bad, f"every permission has a question, a body and a name {bad or ''}")


# ── 3–5. the real buttons ────────────────────────────────────────────

def test_gates() -> None:
    print("\nthe real window")
    from PyQt6.QtWidgets import QApplication

    from core import text_scale
    import ui

    permissions.reset_all()
    permissions.set_walkthrough_done(True)      # no tour over this test

    app = QApplication.instance() or QApplication([])
    ui._init_fonts()
    text_scale.install(app, ui.SIVASAFE_STYLESHEET)
    win = ui.MainWindow(face_path="")
    win.resize(1400, 900)
    win.show()
    app.processEvents()

    def pump(n: int = 6) -> None:
        for _ in range(n):
            app.processEvents()

    def card() -> ui.PermissionOverlay | None:
        return win._perm_overlay

    ran: list[str] = []
    win.on_text_command = lambda cmd: ran.append(cmd)

    # ── a first press is blocked until the card is answered ──
    win._quick_action("open whatsapp")
    pump()
    check(card() is not None, "pressing WhatsApp raises a permission card")
    check(ran == [], "and does not open WhatsApp while the card is unanswered")
    if card() is not None:
        check(card()._perm.key == permissions.WHATSAPP,
              "the card is the WhatsApp one, not a generic 'automation' one")

    # ── Cancel blocks the action and is not written down ──
    if card() is not None:
        card()._cancel.click()
    pump()
    check(ran == [], "Cancel does not run the action")
    check(not permissions.is_granted(permissions.WHATSAPP),
          "Cancel is not stored — it will ask again next time")
    check(card() is None, "the card goes away when it is answered")

    # ── and it does ask again ──
    win._quick_action("open whatsapp")
    pump()
    check(card() is not None, "the second press asks again")

    # ── Allow runs the very press that was interrupted ──
    if card() is not None:
        card()._allow.click()
    pump()
    check(ran == ["open whatsapp"],
          "Allow lets the original press through, without pressing it twice")
    check(permissions.is_granted(permissions.WHATSAPP), "Allow is stored")

    # ── and is never asked for again ──
    ran.clear()
    win._quick_action("open whatsapp")
    pump()
    check(card() is None, "an allowed action asks nothing the second time")
    check(ran == ["open whatsapp"], "and runs straight away")

    # ── a different capability is still its own question ──
    shots: list[int] = []
    win._do_screenshot = lambda: shots.append(1)
    win._quick_action("LOCAL:screenshot")
    pump()
    check(card() is not None, "the screenshot button asks separately")
    check(shots == [], "and takes no picture until it is answered")
    if card() is not None:
        check(card()._perm.key == permissions.SCREEN,
              "and asks about looking at the screen")
        card()._allow.click()
    pump()
    check(shots == [1], "then takes the picture")

    # ── the microphone ──
    check(not win._talk_pressed, "Talk starts up")
    win._on_talk_pressed()
    pump()
    check(card() is not None, "Talk asks for the microphone")
    check(not win._talk_pressed,
          "and does not start listening while the card is unanswered")
    if card() is not None:
        card()._allow.click()
    pump()
    win._on_talk_pressed()
    check(win._talk_pressed, "after Allow, Talk works normally")
    win._on_talk_released()

    # ── one card at a time ──
    permissions.reset_all()
    win._quick_action("open whatsapp")
    win._quick_action("LOCAL:read_screen")
    pump()
    check(card() is not None and len(win._perm_queue) == 1,
          "two presses at once queue rather than stacking two cards")
    if card() is not None:
        card()._cancel.click()
    pump()
    check(card() is not None, "the queued one comes up when the first is answered")
    if card() is not None:
        card()._cancel.click()
    pump()

    # ── from a worker thread ──
    #
    # The path the tool dispatcher uses. A QTimer created off the GUI thread
    # never fires, which is how the safety card once computed a verdict, spoke
    # it, and rendered nothing — with no error anywhere. A queued signal is the
    # mechanism that actually crosses threads, and this is what proves it does.
    permissions.reset_all()
    siva_ui = ui.SivaUI.__new__(ui.SivaUI)
    siva_ui._win = win
    answer: dict = {}

    def worker() -> None:
        answer["got"] = siva_ui.request_permission(permissions.EMAIL)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    for _ in range(200):
        app.processEvents()
        if card() is not None:
            break
    check(card() is not None, "a worker thread's request reaches the GUI thread")
    if card() is not None:
        card()._allow.click()
    for _ in range(200):
        app.processEvents()
        if "got" in answer:
            break
    t.join(timeout=5.0)
    check(answer.get("got") is True, "and the answer travels back to the worker")
    check(permissions.is_granted(permissions.EMAIL), "and is stored")

    # ── the Settings list agrees with the store ──
    page = win._settings_page
    page.refresh_permission_rows()
    _, _, _, state = page._perm_rows[permissions.EMAIL]
    check("Allowed" in state.text(), "Settings shows email as allowed")
    _, _, _, state = page._perm_rows[permissions.SYSTEM]
    check("Not allowed" in state.text(), "Settings shows an unasked one as not allowed")

    # Turning it off from Settings really revokes it.
    page._on_permission_row_clicked(permissions.EMAIL)
    pump()
    check(not permissions.is_granted(permissions.EMAIL),
          "pressing an allowed row in Settings turns it off")

    # ── the demo reset ──
    permissions.grant(permissions.CAMERA)
    permissions.set_walkthrough_done(True)
    win.reset_first_run(run_tour=False)
    check(permissions.granted_keys() == set(),
          "the demo reset forgets every permission")
    check(not permissions.walkthrough_done(),
          "and puts the walkthrough back to not-yet-seen")

    # Deliberately not win.close(): MainWindow.closeEvent is the real shutdown
    # path and ends in TerminateProcess, which would kill this run before it
    # printed its own result. Hiding is enough for an offscreen window.
    win.hide()


# ── the live session's gate ──────────────────────────────────────────

def test_tool_gate() -> None:
    """`SivaLive._permission_ok`, the highest-stakes piece of the whole thing.

    It is awaited inside the receive loop, so a version of it that blocks
    instead of yielding does not fail loudly — it takes the entire live session
    down while the assistant appears to have simply stopped answering. That is
    the failure mode §12 of the handoff describes for the quota ladder, and it
    took a day to find. This runs the real method against a stubbed UI.
    """
    print("\nthe live session's tool gate")
    import asyncio
    import main

    permissions.reset_all()

    class _StubUI:
        def __init__(self, answer, delay=0.0):
            self.answer = answer
            self.delay = delay
            self.asked: list[str] = []

        def request_permission(self, key):
            self.asked.append(key)
            if self.delay:
                import time
                time.sleep(self.delay)
            return self.answer

        def write_log(self, _text):
            pass

    def run(tool, args, ui_stub, wait=None):
        """Run the gate and report (answer, how long the await took).

        Deliberately not asyncio.run(): that drains the default executor before
        it returns, so a stubbed UI still sitting in a sleep makes it look as
        though the gate hung when the gate had in fact already given up and
        handed control back. In the real app the loop keeps turning and only
        the moment the coroutine yields matters, so that is what is timed.
        """
        import time
        live = main.SivaLive.__new__(main.SivaLive)
        live.ui = ui_stub
        if wait is not None:
            live._PERMISSION_WAIT = wait
        loop = asyncio.new_event_loop()
        try:
            t0 = time.time()
            out = loop.run_until_complete(live._permission_ok(tool, args))
            took = time.time() - t0
            # Outside the measurement, but before moving on: an abandoned
            # worker still holding the executor would otherwise be alive during
            # the Qt section below.
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()
        return out, took

    stub = _StubUI(True)
    check(run("open_app", {"app_name": "whatsapp"}, stub)[0] is True,
          "an allowed answer lets the tool through")
    check(stub.asked == [permissions.WHATSAPP],
          "and it asked about WhatsApp, not about programs in general")

    stub = _StubUI(False)
    check(run("screen_process", {}, stub)[0] is False,
          "a refusal stops the tool")

    permissions.grant(permissions.SCREEN)
    stub = _StubUI(False)
    out, _ = run("screen_process", {}, stub)
    check(out is True, "an already-allowed tool runs without asking")
    check(stub.asked == [], "and really does not ask")

    stub = _StubUI(True)
    out, _ = run("shutdown_siva", {}, stub)
    check(out is True and stub.asked == [], "shutting down is never gated")

    # Nobody answers. It has to give up on its own — a tool that never returns
    # is a session that never speaks again.
    permissions.reset_all()
    stub = _StubUI(True, delay=3.0)
    out, took = run("web_search", {}, stub, wait=0.4)
    check(out is False, "an unanswered card is treated as 'not given'")
    check(took < 1.5,
          f"and hands control back at its own deadline (took {took:.2f}s)")

    # The message handed back to the model when the answer was no.
    refusal = main.SivaLive._permission_refusal(
        main.SivaLive.__new__(main.SivaLive), "open_app", {"app_name": "notepad"})
    check("permission" in refusal.lower() and "Settings" in refusal,
          "the refusal tells the model to name the permission and where to change it")

    permissions.reset_all()


def main_() -> int:
    print(f"fake config: {_FAKE}")
    test_store()
    test_tool_map()
    test_tool_gate()
    test_gates()

    print("\n" + "=" * 60)
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  · {p}")
        return 1
    print("clean — every gate consults the store, and the store remembers")
    return 0


if __name__ == "__main__":
    code = main_()
    # os._exit, not sys.exit. What is left standing at this point is a
    # QApplication with live widgets and a closed asyncio loop, and Qt unwinding
    # that tree can abort for reasons that have nothing to do with anything this
    # file checks — which turns the exit code into a coin flip and makes it
    # useless as a check. The result above is already computed and flushed; the
    # app's own exit path is just as abrupt, for related reasons (§5).
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
