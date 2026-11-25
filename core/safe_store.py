"""
Crash-safe JSON writes for everything SivaSafe has to remember.

Path.write_text() truncates the file and then writes it. If the process dies
in that window — a hard exit, a power cut, Task Manager — what is left on disk
is a half-written file, and the assistant loses what it knew.

Writing to a temporary file in the same directory and then renaming it over the
original avoids that. os.replace() is atomic on Windows and POSIX alike, so a
reader either sees the whole previous file or the whole new one, never a
fragment. The cost is one extra create and rename per save, and saves only
happen when the user actually changes something.
"""

import json
import os
from pathlib import Path


def write_json(path, data, *, indent: int = 2, default=None,
               ensure_ascii: bool = True) -> bool:
    """
    Serialise `data` to `path` atomically. Returns True on success.

    Never raises: losing a save must not take the application down with it.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=indent, default=default,
                          ensure_ascii=ensure_ascii)
        # Same directory, so the rename stays on one filesystem and is atomic.
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())      # on disk before it becomes the real file
        os.replace(tmp, p)
        return True
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def write_text(path, text: str) -> bool:
    """Atomic plain-text write, same guarantees as write_json."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        return True
    except Exception:
        return False
