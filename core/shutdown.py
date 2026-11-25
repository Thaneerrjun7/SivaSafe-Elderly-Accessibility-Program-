"""
One way out of SivaSafe.

The app had two exits that behaved differently: the "Shut Down" command called
os._exit(0) and really did stop, while the window's X button relied on Qt
noticing that the last window had closed. With the dashboard server and the
live audio session running that never fired, so app.exec() never returned,
os._exit(0) was never reached, and the process stayed resident holding its
memory and port 8000. Closing and reopening a few times left several dead
copies behind.

Both routes now come here.

Why a hard exit at the end: the process owns a uvicorn server, a Gemini
websocket, mic and playback streams and OpenCV camera handles, several of them
inside daemon threads sitting in blocking C calls that cannot be interrupted
politely. Waiting on them can hang for good. Everything that must survive is
already on disk — every store writes synchronously and atomically the moment
something changes (see core/safe_store.py) — so there is nothing buffered to
lose. Exiting hard is also instant, which keeps the next launch quick.
"""

import os
import sys
import threading
import time

_lock = threading.Lock()
_shutting_down = False

# Time allowed for a save that is already in flight to finish. Writes are
# atomic, so this is belt-and-braces rather than load-bearing.
_GRACE_SECONDS = 0.25


def is_shutting_down() -> bool:
    return _shutting_down


def _hard_kill(exit_code: int) -> None:
    """
    End this process now, without ExitProcess.

    os._exit() calls ExitProcess(), which SUSPENDS every other thread and then
    tries to unwind them. This process owns PortAudio callbacks, a websocket,
    a uvicorn server and an asyncio loop; if any of them is inside a driver
    call, that unwind can hang forever. And because ExitProcess suspends the
    other threads first, a watchdog thread *inside* this process is frozen too
    and cannot rescue it — which is exactly how an earlier attempt at this
    failed.

    TerminateProcess is serviced by the kernel. It does not run DLL detach
    handlers, does not wait on any thread, and needs no user-mode lock, so it
    works from anywhere — including from inside the non-client modal loop that
    Windows runs while the title-bar X is being clicked.

    The argtypes/restype below are load-bearing, not decoration. A HANDLE is
    pointer-sized; with no signature declared, ctypes types the return of
    GetCurrentProcess as a 32-bit int and passes it back in a 32-bit slot, so
    the callee reads a truncated handle. TerminateProcess then returns 0 and
    does nothing at all — no exception, no message. Execution fell through to
    the os._exit() below, which is the precise call this whole function exists
    to avoid, and the process was left with its threads suspended mid-unwind:
    window gone, one thread, several hundred MB resident, still holding
    port 8000. Verified: without these five lines TerminateProcess returns 0
    and the process survives; with them the process exits immediately.
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL
        if k32.TerminateProcess(k32.GetCurrentProcess(), exit_code):
            return                      # unreachable; the process is already gone
        print(f"[SIVA] TerminateProcess failed (err "
              f"{ctypes.get_last_error()}) — falling back to os._exit",
              flush=True)
    except Exception as e:
        try:
            print(f"[SIVA] hard kill unavailable ({e}) — falling back to "
                  f"os._exit", flush=True)
        except Exception:
            pass
    # Only reached if TerminateProcess genuinely failed. Say so above rather
    # than arriving here in silence, which is how this went unnoticed.
    os._exit(exit_code)


def _arm_force_kill(exit_code: int, delay: float = 1.5) -> None:
    """
    Backstop for a hang in the *graceful* steps below (a stuck save, say),
    where the other threads are still running normally and this can fire.

    It deliberately cannot save us from a hang inside ExitProcess — hence
    _hard_kill, which avoids ExitProcess in the first place.
    """
    def killer():
        time.sleep(delay)
        _hard_kill(exit_code)

    threading.Thread(target=killer, daemon=True).start()


def shutdown(reason: str = "", ui=None, exit_code: int = 0) -> None:
    """
    Stop SivaSafe. Safe to call from any thread, and only ever runs once.
    """
    global _shutting_down
    with _lock:
        if _shutting_down:
            return
        _shutting_down = True

    # Armed first, so even a hang in the steps below cannot leave the process
    # alive and unresponsive.
    _arm_force_kill(exit_code)

    try:
        print(f"[SIVA] Shutting down ({reason or 'requested'})...", flush=True)
    except Exception:
        pass

    # Persist anything the UI holds in memory but has not been asked to save.
    # Accepts either the MainWindow or the SivaUI wrapper around it.
    try:
        win = getattr(ui, "_win", ui)
        log = getattr(win, "_log", None)
        if log is not None and hasattr(log, "_save_history"):
            log._save_history()
    except Exception:
        pass

    # Let an in-flight atomic save land before the process disappears.
    # Runs after the window is already hidden, so it is never perceived as lag.
    # (cv2.destroyAllWindows() used to be here; it touches user32 and is
    # pointless when the process is about to exit anyway — exactly the kind of
    # call that risks wedging during teardown.)
    time.sleep(_GRACE_SECONDS)

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    _hard_kill(exit_code)
