"""
User preference persistence layer — stores accessibility and display settings
in a JSON file next to the app config.

Location: config/preferences.json

Deliberately separate from config/api_keys.json, which holds API secrets and
password hashes. A corrupted or rewritten preferences file must never be able
to take credentials with it.
"""

import json
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _store_path() -> Path:
    d = _base_dir() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d / "preferences.json"


# ── Sign-in mode ─────────────────────────────────────────────────────
#
# Three settings, and deliberately only three. Face recognition on its own is
# not offered: a face is not a secret, it can be presented by a photograph or
# by someone who simply looks similar, and there is no way to change it once
# it fails. So the face check is only ever an accelerator in front of the
# password — never a replacement for it.

AUTH_KEY = "auth_mode"
AUTH_OFF = "off"                     # no sign-in at all
AUTH_PASSWORD = "password"           # password only
AUTH_FACE_PASSWORD = "face_password"  # face scan, password as the fallback

_AUTH_MODES = (AUTH_OFF, AUTH_PASSWORD, AUTH_FACE_PASSWORD)


def auth_mode() -> str:
    """
    The sign-in mode, falling back to the strictest one.

    Anything unrecognised — a hand-edited file, a truncated write, a value from
    a newer version — reads as full face-and-password. A damaged preferences
    file must never be able to switch the sign-in off.
    """
    m = get(AUTH_KEY, AUTH_FACE_PASSWORD)
    return m if m in _AUTH_MODES else AUTH_FACE_PASSWORD


def set_auth_mode(mode: str) -> bool:
    if mode not in _AUTH_MODES:
        return False
    return set(AUTH_KEY, mode)


def load_all() -> dict:
    """Read the whole preference dict. Never raises."""
    try:
        p = _store_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get(key: str, default=None):
    """Read one preference, falling back to default on any problem."""
    return load_all().get(key, default)


def set(key: str, value) -> bool:
    """
    Write one preference, preserving every other key already on disk.
    Returns True on success. Never raises.
    """
    try:
        data = load_all()
        data[key] = value
        p = _store_path()
        # Write to a sibling temp file first so a crash mid-write cannot leave
        # a half-written preferences file behind.
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception:
        return False
