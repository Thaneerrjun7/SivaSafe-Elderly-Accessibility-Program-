"""
Restart SivaSafe: wait for the old process to die, then start a new one.

Spawned detached by the running app just before it shuts itself down. It exists
for one reason: **the new instance must not start while the old one is still
alive.** The old process holds port 8000 until the moment it exits, and a new
instance that starts too early finds the port taken — its dashboard dies, and
Remote Control is silently unavailable for the whole session.

So this waits for the old process id to disappear before launching. That is a
restart, not a reset: nothing is deleted here, and everything the app remembers
— settings, reminders, tasks, medications, long-term memory and the chat log —
is on disk already and is simply read back by the new instance.

    python scripts/relaunch.py <old_pid>
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# Generous, because it is a ceiling and not a delay: the loop exits the moment
# the old process is gone, which is normally well under a second.
MAX_WAIT_SECONDS = 30
POLL_SECONDS = 0.2


def _alive(pid: int) -> bool:
    """Is that process id still running?"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
                # Polled every 200ms while the old instance shuts down; without
                # this it strobes a console window the whole time.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)          # signal 0 tests for existence only
        return True
    except OSError:
        return False


def main() -> int:
    old_pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0

    deadline = time.time() + MAX_WAIT_SECONDS
    while old_pid and _alive(old_pid) and time.time() < deadline:
        time.sleep(POLL_SECONDS)

    # A moment more: the process object can be gone a beat before Windows has
    # actually released its listening socket.
    time.sleep(1.0)

    base = Path(__file__).resolve().parent.parent
    try:
        # Spelled out here rather than relying on core.proc, which patches the
        # process it is installed in and this script is its own process. It is
        # started detached, so it has no console to lend the new instance —
        # without this, restarting flashes one up.
        subprocess.Popen(
            [sys.executable, str(base / "main.py")], cwd=str(base),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"[relaunch] could not start SIVA again: {e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
