"""
Two things must both be true:
  1. Pressing X actually ends the process.
  2. Nothing the assistant remembers is disturbed by that.

Writes a known marker into every persistent store, builds the real UI, closes
the window exactly as the X button does, and the parent then checks the files.

Run as:  shutdown_test.py child   (does the work, should exit by itself)
         shutdown_test.py         (drives the child and verifies)
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = r"C:\Users\Arjun Shanker\Downloads\Mark-XLVI-main\Mark-XLVI-main"
PY = r"C:\Users\Arjun Shanker\Downloads\Mark-XLVI-main\.venv\Scripts\python.exe"
sys.path.insert(0, ROOT)

MARK = f"shutdown-probe-{int(time.time())}"
HOME = Path.home()

# Imported rather than written out again. This path was hard-coded as
# ~/.siva/memory.json, which is not where long-term memory lives — so the
# store this harness exists to protect was reported MISSING on every run and
# never actually checked.
from memory.memory_manager import MEMORY_PATH as _MEMORY_PATH

# marker=True means the child deliberately wrote MARK into this store, so its
# absence afterwards is a failure, not a curiosity.
STORES = {
    "long-term memory": (_MEMORY_PATH, True),
    "chat history": (HOME / ".siva" / "chat_history.json", False),
    "reminders": (HOME / ".siva" / "reminders" / "reminders.json", False),
    "tasks": (HOME / ".siva" / "tasks" / "tasks.json", True),
    "text size pref": (Path(ROOT) / "config" / "preferences.json", False),
}


def child(mark):
    os.chdir(ROOT)
    from core import crash_guard
    crash_guard.install()

    from memory.memory_manager import load_memory, save_memory
    mem = load_memory()
    mem.setdefault("_probe", {})["marker"] = mark
    save_memory(mem)

    from actions import task_store
    task_store.save_task(f"probe {mark}", False, notes="shutdown probe")

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    import ui

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    siva = ui.SivaUI("face.png")

    def press_x():
        print("CHILD: closing window via close() == the X button", flush=True)
        sys.stdout.flush()
        siva._win.close()

    QTimer.singleShot(2500, press_x)
    # If shutdown fails, this proves the hang rather than hiding it.
    QTimer.singleShot(12000, lambda: (print("CHILD: STILL ALIVE 9.5s AFTER CLOSE",
                                            flush=True), os._exit(99)))
    siva.root.mainloop()


if len(sys.argv) > 1 and sys.argv[1] == "child":
    child(sys.argv[2])
    sys.exit(0)

# ── parent ────────────────────────────────────────────────────────────────
print(f"marker: {MARK}\n")
t0 = time.time()
proc = subprocess.Popen([PY, "-u", __file__, "child", MARK],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
try:
    out, _ = proc.communicate(timeout=90)
except subprocess.TimeoutExpired:
    proc.kill()
    out = "(timed out — process never exited)"
elapsed = time.time() - t0

for line in (out or "").splitlines():
    if "CHILD:" in line or "Shutting down" in line or "Traceback" in line:
        print("   ", line)

print(f"\nexit code: {proc.returncode}   (0 = clean)")
print(f"time from launch to process gone: {elapsed:.1f}s")
print("process still running:", proc.poll() is None)

print("\n--- persistent stores after the hard exit ---")
ok = True
for name, (path, wants_mark) in STORES.items():
    if not path.exists():
        # A store that is not there has not survived anything. This used to
        # print and carry on, which let the whole harness report PASS while
        # the file it was asked about did not exist.
        ok = False
        print(f"   {name:<18} MISSING (FAIL)  {path}")
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        found = MARK in json.dumps(data)
        note = ""
        if wants_mark and not found:
            # Valid JSON is not the same as intact. The child wrote the marker
            # into this store; if the write was lost the file can still parse
            # perfectly and still have failed at its job.
            ok = False
            note = "  <- MARKER LOST (FAIL)"
        elif found:
            note = "  <- marker survived"
        print(f"   {name:<18} valid JSON, {path.stat().st_size:>6} bytes{note}")
    except Exception as e:
        ok = False
        print(f"   {name:<18} CORRUPT: {e}")

strays = list((HOME / ".siva").rglob("*.tmp")) + \
         list((Path(ROOT) / "config").glob("*.tmp"))
print(f"\nleftover .tmp files: {len(strays)}")


def _clean_up():
    """
    Take the probe data back out.

    These are the user's live stores, not fixtures — the marker has to go into
    the real files for the test to mean anything, so it also has to come out
    afterwards. Without this the harness leaves a "probe shutdown-probe-…"
    entry sitting in the Tasks page, which is a poor thing to find during a
    demonstration.
    """
    from core.safe_store import write_json
    try:
        tp = HOME / ".siva" / "tasks" / "tasks.json"
        if tp.exists():
            tasks = json.loads(tp.read_text(encoding="utf-8"))
            kept = [t for t in tasks
                    if not str(t.get("text", "")).startswith("probe shutdown-probe-")]
            if len(kept) != len(tasks):
                write_json(tp, kept, indent=2, default=str)
    except Exception as e:
        print(f"   (could not tidy tasks: {e})")
    try:
        if _MEMORY_PATH.exists():
            mem = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            if mem.pop("_probe", None) is not None:
                write_json(_MEMORY_PATH, mem, indent=2, default=str)
    except Exception as e:
        print(f"   (could not tidy memory: {e})")


_clean_up()
print("probe data removed from the live stores")
print("\nRESULT:", "PASS" if (ok and proc.returncode == 0) else "FAIL")
