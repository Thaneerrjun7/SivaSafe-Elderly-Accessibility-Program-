"""
Does the screen actually reach the model, and does the model actually read it?

Every other check of this path has been "run it and see if SIVA says something
plausible", which cannot tell the difference between a model that read the
screen and a model that guessed. A guess about a desktop is right often enough
to pass by eye. So the middle test here puts a random five-digit code on the
screen, captures it through the real capture path, and requires the code to
come back in the transcript. Nothing but reading the pixels produces that
number.

Four things get checked, cheapest first, and it stops at the first one that
makes the rest meaningless:

  1  OFFLINE   imports, keys, permission wiring, a real screen grab measured
               for size and for not being blank. No network.
  2  MODEL     a made-up test card straight to the live session — proves the
               session connects and the model reads pixels, without depending
               on anything being on the screen.
  3  SCREEN    the same code shown fullscreen and captured with mss, through
               capture_screen() exactly as the Read Screen button calls it.
               This is the one that proves the *screen* path, end to end.
  4  VOICE     screen_process(), the entry the assistant itself calls, on
               whatever is really on screen. Ground truth is not available
               here, so this one is printed for a human to read.

Run it:

    .venv\\Scripts\\python.exe devtools\\vision_check.py

  --mute        don't play the spoken reply (still checks the transcript)
  --camera      also capture from the webcam (off by default: slow, and the
                demo is about the screen)
  --quick       stages 1 and 2 only, no fullscreen window, ~20s

SIVA speaks the answers out loud unless --mute. That is deliberate: the audio
path is part of what a demo needs working, and a silent pass would hide a dead
output device.
"""
from __future__ import annotations

import argparse
import io
import os
import random
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT  = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:                                    # emoji in the module's own log lines
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RESULTS: list[tuple[str, bool, str]] = []
WARNINGS: list[tuple[str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   — {detail}" if detail else ""),
          flush=True)
    return bool(ok)


def warn(label: str, ok: bool, detail: str) -> bool:
    """Something to know about that is not something being broken.

    Kept out of the pass/fail count on purpose. A check that shouts about a
    permission the user has simply not answered yet trains them to ignore the
    last line of the report, which is the one line that has to mean something
    at nine in the morning.
    """
    if ok:
        print(f"  ok    {label}", flush=True)
        return True
    WARNINGS.append((label, detail))
    print(f"  NOTE  {label}   — {detail}", flush=True)
    return False


def head(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}", flush=True)


class Listener:
    """Stands in for the UI so the spoken answer can be read as text.

    `_recv_loop` writes each finished turn to `player.write_log`, which is the
    only place the transcript surfaces — the audio itself is not something a
    test can assert on.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._evt = threading.Event()

    def write_log(self, text: str) -> None:
        print(f"     [SIVA] {text}", flush=True)
        self.lines.append(text)
        self._evt.set()

    def arm(self) -> None:
        self._evt.clear()
        self.lines.clear()

    def wait(self, timeout: float) -> str:
        """The transcript of the next finished turn, or '' if none arrives."""
        if not self._evt.wait(timeout=timeout):
            return ""
        # A turn can land in more than one log line if the model pauses; give
        # the tail of it a moment to arrive before reading.
        time.sleep(1.5)
        return " ".join(self.lines)


def test_card(code: str) -> bytes:
    """A picture whose content cannot be guessed. Returns PNG bytes."""
    from PIL import Image, ImageDraw, ImageFont

    img  = Image.new("RGB", (1600, 900), (255, 255, 255))
    d    = ImageDraw.Draw(img)
    font = None
    for name in ("arialbd.ttf", "arial.ttf", "seguisb.ttf"):
        try:
            font = ImageFont.truetype(name, 260)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    d.text((800, 380), code, fill=(0, 0, 0), font=font, anchor="mm")
    small = ImageFont.truetype("arial.ttf", 60) if font else None
    try:
        d.text((800, 640), "SIVA VISION TEST", fill=(90, 90, 90),
               font=small, anchor="mm")
    except Exception:
        pass

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── 1. offline ───────────────────────────────────────────────────────

def stage_offline(args) -> bool:
    head("1  OFFLINE — imports, keys, permissions, a real screen grab")

    ok = True
    for mod in ("mss", "PIL", "numpy", "sounddevice", "google.genai"):
        try:
            __import__(mod)
            check(f"import {mod}", True)
        except Exception as e:
            ok = check(f"import {mod}", False, str(e)) and ok

    import cv2  # noqa: F401  — camera only, but a broken install is worth knowing
    check("import cv2 (camera path)", True)

    from config import get_vision_api_keys, get_live_api_keys
    vkeys = get_vision_api_keys()
    ok = check("vision API keys configured", bool(vkeys),
               f"{len(vkeys)} key(s) on the ladder") and ok
    if vkeys and get_live_api_keys() and vkeys[0] == get_live_api_keys()[0]:
        check("vision key is separate from the conversation key", False,
              "same key for both — see the note at the end")

    from core import permissions
    ok = check("screen_process is gated on the screen permission",
               permissions.for_tool("screen_process", {}) == permissions.SCREEN) and ok
    check("screen permission already granted",
          permissions.is_granted(permissions.SCREEN),
          "if this is FAIL, the first request in the demo pops a permission card")

    import sounddevice as sd
    try:
        dev = sd.query_devices(kind="output")
        check("an audio output device exists", True, dev["name"])
    except Exception as e:
        ok = check("an audio output device exists", False, str(e)) and ok

    from actions import screen_processor as sp

    # The camera backend used to raise NameError here — _get_os was called but
    # never imported, so every 'look at my camera' died in the capture.
    try:
        check("camera backend resolves", isinstance(sp._cv2_backend(), int),
              f"backend={sp._cv2_backend()}")
    except Exception as e:
        ok = check("camera backend resolves", False, f"{type(e).__name__}: {e}") and ok

    t0 = time.perf_counter()
    try:
        img, mime = sp.capture_screen()
    except Exception as e:
        return check("capture_screen()", False, f"{type(e).__name__}: {e}") and False
    dt = time.perf_counter() - t0

    ok = check("capture_screen() returns JPEG bytes",
               mime == "image/jpeg" and len(img) > 2_000,
               f"{len(img):,} bytes in {dt * 1000:.0f}ms") and ok

    from PIL import Image, ImageStat
    im = Image.open(io.BytesIO(img))
    ok = check("capture is within the size the model is sent",
               im.width <= sp._IMG_MAX_W and im.height <= sp._IMG_MAX_H,
               f"{im.width}x{im.height}") and ok

    # A black frame is what a failed grab looks like, and it is still valid
    # JPEG — the model would describe an empty screen and nothing would look
    # broken until someone asked why.
    sd_ = max(ImageStat.Stat(im.convert("L")).stddev)
    ok = check("capture is not a blank frame", sd_ > 6.0, f"stddev={sd_:.1f}") and ok

    path = os.path.join(OUT, "VISION_capture.jpg")
    open(path, "wb").write(img)
    print(f"     saved what the model will be sent → {os.path.basename(path)}")

    if args.camera:
        try:
            cam, cmime = sp._capture_camera()
            check("camera capture", len(cam) > 2_000, f"{len(cam):,} bytes {cmime}")
            open(os.path.join(OUT, "VISION_camera.jpg"), "wb").write(cam)
        except Exception as e:
            check("camera capture", False, f"{type(e).__name__}: {e}")

    return ok


# ── 2. the model reads pixels ────────────────────────────────────────

def stage_model(listener) -> bool:
    head("2  MODEL — a code only a reader could know, straight to the session")

    from actions import screen_processor as sp

    code = f"{random.randint(10000, 99999)}"
    png  = test_card(code)
    jpg, mime = sp._compress(png, "PNG")
    print(f"     test card shows {code}  ({len(jpg):,} bytes after compression)")

    t0 = time.perf_counter()
    ok = sp.analyze_image(
        jpg, mime,
        "Read the number in this image. Reply with the digits only.",
        player=listener,
    )
    if not check("session connected and image queued", ok,
                 f"{time.perf_counter() - t0:.1f}s"):
        return False

    said = listener.wait(60.0)
    if not check("the model answered", bool(said.strip()),
                 f"{time.perf_counter() - t0:.1f}s to the spoken reply"):
        return False

    return check("the answer contains the code from the picture",
                 code in said.replace(",", "").replace(" ", "") or code in said,
                 f"looking for {code}")


# ── 3. the screen path, with ground truth ────────────────────────────

def stage_screen(listener) -> bool:
    head("3  SCREEN — the same trick, but read off the real screen")

    from actions import screen_processor as sp
    from PyQt6.QtWidgets import QApplication, QLabel
    from PyQt6.QtCore import Qt

    code = f"{random.randint(10000, 99999)}"
    app  = QApplication.instance() or QApplication(sys.argv)
    lab  = QLabel(f"{code}")
    lab.setStyleSheet("background:#ffffff; color:#000000; font-size:300px; font-weight:700;")
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    lab.showFullScreen()

    # Let the compositor actually paint it before mss looks. Same reason ui.py
    # waits after hiding its own window before taking a screenshot.
    end = time.time() + 1.2
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

    try:
        img, mime = sp.capture_screen()          # the real path, mss and all
    finally:
        lab.close()
        app.processEvents()

    print(f"     screen shows {code}  ({len(img):,} bytes captured)")
    open(os.path.join(OUT, "VISION_screen_testcard.jpg"), "wb").write(img)

    listener.arm()
    t0 = time.perf_counter()
    sp.analyze_image(img, mime,
                     "Read the number on this screen. Reply with the digits only.",
                     player=listener)
    said = listener.wait(60.0)
    if not check("the model answered", bool(said.strip()),
                 f"{time.perf_counter() - t0:.1f}s"):
        return False

    return check("what was on the screen is what the model read",
                 code in said.replace(",", "").replace(" ", "") or code in said,
                 f"looking for {code}")


# ── 4. the entry the assistant calls ─────────────────────────────────

def stage_voice(listener) -> bool:
    head("4  VOICE — screen_process(), on whatever is really on screen")

    from actions import screen_processor as sp

    listener.arm()
    t0 = time.perf_counter()
    ok = sp.screen_process(
        {"angle": "screen",
         "text": "In one short sentence, what application is on this screen?"},
        player=listener,
    )
    if not check("screen_process() accepted the call", ok):
        return False

    said = listener.wait(60.0)
    ok = check("it spoke an answer about the screen", bool(said.strip()),
               f"{time.perf_counter() - t0:.1f}s")
    print("\n     Read this and check it matches what is actually on your screen:")
    print(f"     \"{said.strip() or '(nothing)'}\"")

    # The two arguments that must be rejected rather than half-handled.
    check("a call with no question is refused",
          sp.screen_process({"angle": "screen"}, player=listener) is False)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mute",   action="store_true", help="no spoken output")
    ap.add_argument("--camera", action="store_true", help="also test the webcam")
    ap.add_argument("--quick",  action="store_true", help="stages 1 and 2 only")
    args = ap.parse_args()

    print(f"vision_check — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"python: {sys.executable}")

    if args.mute:
        import sounddevice as sd

        class _Silent:
            def __init__(self, *a, **k): pass
            def start(self): pass
            def write(self, chunk): pass
            def stop(self): pass
            def close(self): pass

        sd.RawOutputStream = _Silent
        print("muted: the reply is checked as text only")

    if not stage_offline(args):
        head("STOPPED — the offline checks failed, so the live ones would only "
             "add noise")
        return 1

    listener = Listener()
    if not stage_model(listener):
        head("STOPPED — the model did not read a picture handed straight to it, "
             "so the screen path cannot be the problem")
        return 1

    if not args.quick:
        stage_screen(listener)
        stage_voice(listener)

    head("RESULT")
    bad = [r for r in RESULTS if not r[1]]
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label}" + (f"   — {detail}" if detail else ""))
    print(f"\n  {len(RESULTS) - len(bad)}/{len(RESULTS)} checks passed")
    print("  " + ("READY" if not bad else "NOT READY — see the failures above"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
