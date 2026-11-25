"""
"Not responding" means the GUI thread stopped pumping Windows messages for
~5 seconds. So measure exactly that: a heartbeat on the GUI thread, recording
every gap. Any gap over a second is a window that cannot answer a click on X.

Also confirms whether SivaLive actually connected, so we know the audio
streams / websocket / dashboard were really running during the measurement.
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(OUT, "gui_resp.log")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

_t0 = time.perf_counter()
_lines = []
_gaps = []


def mark(msg):
    _lines.append(f"{(time.perf_counter() - _t0) * 1000:9.1f} ms  {msg}")
    _flush()


def _flush():
    try:
        with open(LOG, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines) + "\n")
            if _gaps:
                worst = sorted(_gaps, reverse=True)[:12]
                fh.write("\nworst GUI-thread stalls (ms): "
                         + ", ".join(f"{g:.0f}" for g in worst) + "\n")
                over1s = [g for g in _gaps if g > 1000]
                over5s = [g for g in _gaps if g > 5000]
                fh.write(f"stalls over 1s: {len(over1s)}   over 5s: {len(over5s)}\n")
                fh.write(f"total heartbeats: {len(_gaps)}\n")
    except Exception:
        pass


import core.auth as auth_mod
auth_mod.AuthGate.run = lambda self: True

import ui as ui_mod
from PyQt6.QtCore import QTimer

_orig_init = ui_mod.SivaUI.__init__
_real_exit = os._exit


def patched_init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    mark("SivaUI constructed")

    state = {"last": time.perf_counter()}

    def beat():
        now = time.perf_counter()
        gap = (now - state["last"]) * 1000
        state["last"] = now
        _gaps.append(gap)
        if gap > 1000:
            mark(f"  GUI STALL: {gap:.0f} ms with no message processing")

    hb = QTimer()
    hb.timeout.connect(beat)
    hb.start(50)
    self._hb = hb            # keep alive

    def press_x():
        mark("=== simulating X press ===")
        t = time.perf_counter()
        self._win.close()
        mark(f"win.close() returned after {(time.perf_counter() - t) * 1000:.1f} ms")

    QTimer.singleShot(30000, press_x)

    def watchdog():
        time.sleep(50)
        mark("!!! WATCHDOG — never exited")
        _flush()
        _real_exit(99)

    threading.Thread(target=watchdog, daemon=True).start()


ui_mod.SivaUI.__init__ = patched_init

mark("starting main()")
import main
main.main()
