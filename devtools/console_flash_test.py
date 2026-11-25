"""
Nothing flashes a console window while SivaSafe starts.

    python devtools/console_flash_test.py            # launch and watch
    python devtools/console_flash_test.py --seconds 40

Launches the app the way a user does — `SivaSafe.exe`, no terminal behind it —
and polls the desktop's top-level windows every 40ms for the whole startup,
recording any console that appears. A flash lasting a tenth of a second is
plainly visible and would be caught; the window does not have to still be there
when this finishes.

Why polling and not something cleverer: the windows in question live for as
little as a few hundred milliseconds, so there is nothing to inspect after the
fact. Watching for them as they happen is the only way to see what the user
sees.

This is the check for the fault where the *launcher* is what exposes the bug.
Under an IDE, `python.exe` already owns a console and every child quietly
inherits it, so none of this is visible; under `pythonw.exe` each one is
allocated a console of its own and put on screen. Running this from an IDE
proves nothing — it launches the exe precisely so the app is consoleless.

Exit code is 0 when clean, 1 otherwise. The app is closed again at the end.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_EXE = _ROOT / "SivaSafe.exe"

POLL_SECONDS = 0.04

# Console hosts. conhost is the classic one; the Terminal-hosted console and
# the PowerShell/cmd frames each have their own class name.
CONSOLE_CLASSES = {
    "ConsoleWindowClass",
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "PseudoConsoleWindow",
}

_user32 = ctypes.windll.user32
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                      wintypes.LPARAM)


def _visible_consoles() -> dict[int, str]:
    """Every visible console window on the desktop right now, by handle."""
    found: dict[int, str] = {}

    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in CONSOLE_CLASSES:
            title = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, title, 256)
            found[hwnd] = f"{cls.value}  {title.value!r}"
        return True

    _user32.EnumWindows(_EnumWindowsProc(_cb), 0)
    return found


def _running() -> list[int]:
    """
    Process ids of the app itself — `main.py`, or the launcher exe.

    Matched on `main.py` rather than on the project path, and with this
    process excluded: run from the project root, *this script* has the project
    path in its own command line and would otherwise report itself as the app
    already running.
    """
    import os

    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or "
         "Name='python.exe' or Name='SivaSafe.exe'\" | "
         "Where-Object { $_.Name -eq 'SivaSafe.exe' -or "
         "$_.CommandLine -like '*main.py*' } | "
         "ForEach-Object { $_.ProcessId }"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    mine = os.getpid()
    return [int(line) for line in out.split()
            if line.strip().isdigit() and int(line) != mine]


def _detector_works() -> bool:
    """
    Prove the watcher can see a flash before trusting it not to see one.

    Reproduces the fault deliberately: a windowless `pythonw.exe` shelling out
    to a console command with no `CREATE_NO_WINDOW`, which is precisely what
    the app used to do ~119 times. If this does not show up, a clean run below
    means nothing and the whole check is worthless.
    """
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        print("  (no pythonw.exe beside this interpreter — cannot self-test)")
        return False

    before = set(_visible_consoles())
    # No creationflags anywhere: the child gets a console of its own, on screen.
    bait = subprocess.Popen(
        [str(pythonw), "-c",
         "import subprocess; subprocess.run(['cmd', '/c', 'ping -n 3 127.0.0.1'],"
         " capture_output=True)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    caught = False
    started = time.monotonic()
    while time.monotonic() - started < 6.0:
        if set(_visible_consoles()) - before:
            caught = True
            break
        time.sleep(POLL_SECONDS)
    try:
        bait.wait(timeout=10)
    except subprocess.TimeoutExpired:
        bait.kill()
    return caught


def _launcher_pids() -> list[int]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-Process SivaSafe -ErrorAction SilentlyContinue | "
         "ForEach-Object { $_.Id }"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return [int(line) for line in out.split() if line.strip().isdigit()]


def _kill(pids: list[int]) -> None:
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> int:
    if sys.platform != "win32":
        print("Windows only — nothing to check.")
        return 0

    print("self-test: can this see a console flash at all?")
    if not _detector_works():
        print("FAILED — the watcher missed a console it was meant to catch. "
              "A clean result would prove nothing, so this is not a pass.")
        return 1
    print("  yes — a deliberate flash was caught\n")
    if not _EXE.exists():
        print(f"FAILED — {_EXE} not found. Build it first:\n"
              f"  powershell -ExecutionPolicy Bypass -File launcher\\build.ps1")
        return 1

    watch_for = 30.0
    if "--seconds" in sys.argv:
        watch_for = float(sys.argv[sys.argv.index("--seconds") + 1])

    if _running():
        print("FAILED — SivaSafe is already running. Close it first; the "
              "launcher would refuse to start a second copy and this would "
              "watch nothing.")
        return 1

    before = set(_visible_consoles())
    print(f"consoles already open (ignored): {len(before)}")
    print(f"launching {_EXE.name} and watching for {watch_for:.0f}s…\n")

    subprocess.Popen([str(_EXE)], creationflags=getattr(
        subprocess, "CREATE_NO_WINDOW", 0))

    seen: dict[str, float] = {}
    started = time.monotonic()
    polls = 0
    try:
        while time.monotonic() - started < watch_for:
            for hwnd, what in _visible_consoles().items():
                if hwnd in before:
                    continue
                if what not in seen:
                    at = time.monotonic() - started
                    seen[what] = at
                    print(f"  {at:5.1f}s  console appeared: {what}")
            polls += 1
            time.sleep(POLL_SECONDS)
    finally:
        # The launcher first. It watches its child's exit code and raises a
        # dialog on anything non-zero — kill the app out from under it and it
        # correctly reports a crash that this harness caused.
        _kill(_launcher_pids())
        time.sleep(0.4)
        _kill(_running())

    print(f"\n{polls} polls over {watch_for:.0f}s "
          f"(every {POLL_SECONDS * 1000:.0f}ms)")
    if seen:
        print(f"\nFAILED — {len(seen)} console window(s) appeared during startup")
        for what, at in sorted(seen.items(), key=lambda kv: kv[1]):
            print(f"  {at:5.1f}s  {what}")
        return 1
    print("\nPASS — no console window appeared while the app started")
    return 0


if __name__ == "__main__":
    sys.exit(main())
