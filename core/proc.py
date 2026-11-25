"""
No console windows flashing over the interface.

The problem
───────────
SIVA shells out constantly — `netsh` and `powershell` to check the firewall on
startup, `schtasks` to register a reminder, `nircmd` for the volume, `pip` to
install a missing package. On Windows, when a process that **has no console of
its own** starts a console program, Windows allocates a fresh console for the
child and puts it on screen. It is visible for as long as the command runs:
long enough to see, and to look broken.

Under an IDE this never happens, because `python.exe` already owns a console
and children quietly inherit it. Launched properly — from `SivaSafe.exe`, which
runs `pythonw.exe` so there is no terminal behind the app — every one of those
calls is a black rectangle flickering over the interface during startup. Same
code, different launcher; the bug only appears in the way a user actually opens
it.

The fix
───────
`CREATE_NO_WINDOW` on the child. There are ~130 places that spawn something,
eleven of which remembered it, and a twelfth would be forgotten next week. So
rather than patch each call, this installs the flag as the **default for the
whole process**: anything that does not say what it wants about windows does
not get one. Third-party libraries that shell out are covered by the same
stroke, which no amount of editing this tree would have achieved.

Asking for a window
───────────────────
Pass `creationflags=` yourself and this leaves the call completely alone —
including `creationflags=proc.SHOW_WINDOW`, which is what `open_app` uses,
because "open the command prompt" has to actually show a command prompt.
"""
from __future__ import annotations

import os
import subprocess

# From the Windows CreateProcess docs. Named here rather than taken from
# `subprocess`, which only defines them on Windows.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008

#: Explicit opt-out. Zero on purpose — it changes nothing about how the child
#: is created, and only marks the call as having made up its own mind, which
#: is what stops the default below from being applied.
SHOW_WINDOW = 0

_installed = False
_original_init = None


def install() -> None:
    """
    Make a hidden console the default for every subprocess in this process.

    Call once, early. Safe to call again — the second call does nothing rather
    than wrapping the wrapper, which would otherwise stack a layer per call and
    make the flags harder to reason about than the problem they solve.

    A no-op off Windows, where none of this exists.
    """
    global _installed, _original_init

    if _installed or os.name != "nt":
        return

    _original_init = subprocess.Popen.__init__

    def _patched_init(self, *args, **kwargs):
        if _wants_default_window(args, kwargs):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        return _original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _patched_init
    _installed = True


def _wants_default_window(args: tuple, kwargs: dict) -> bool:
    """
    Should this call get the flag?

    Only when the caller said nothing at all about how the child should be
    created. Anyone who passed `creationflags` or a `startupinfo` has already
    decided — including the code that asks for `CREATE_NEW_CONSOLE`, which
    Windows refuses to combine with `CREATE_NO_WINDOW` and which would raise
    if this were less careful, and the relaunch path that uses
    `DETACHED_PROCESS` to outlive the process starting it.
    """
    if "creationflags" in kwargs or "startupinfo" in kwargs:
        return False
    # creationflags is the 13th positional parameter of Popen. Nobody in this
    # tree passes it that way, but a library might, and silently overriding it
    # would be worse than doing nothing.
    if len(args) > 12:
        return False
    return True
