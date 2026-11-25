"""
Password recovery codes.

When someone has genuinely forgotten their password, the only way back in is a
six-digit code sent to the recovery address on file. This module owns that
code's whole life — making it, storing it, checking it, expiring it — and
nothing else. It deliberately knows nothing about Qt or about sending mail, so
it can be tested on its own without a window or a network.

Three rules it exists to enforce:

* **The plaintext code never reaches disk.** Only a PBKDF2 hash is stored, the
  same treatment the password itself gets. Anyone reading `api_keys.json` sees
  nothing they can type in.
* **A code dies quickly and is used once.** Ten minutes, then it is worthless;
  accepted once, then it is gone.
* **Sending is rate-limited.** Five an hour. Without that, the "email me a
  code" button is a way to empty SIVA's mail quota, or to bury a real user
  under messages.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

from core import safe_store

# Matches core/auth.py — the same work factor guards the code as the password.
_PBKDF2_ITERS = 600_000

CODE_DIGITS = 6
CODE_TTL_SECONDS = 10 * 60      # ten minutes
MAX_SENDS_PER_HOUR = 5

_KEY_HASH = "recovery_code_hash"
_KEY_SALT = "recovery_code_salt"
_KEY_EXPIRES = "recovery_code_expires"
_KEY_SENDS = "recovery_sends"
_KEY_EMAIL = "recovery_email"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _config_path() -> Path:
    return _base_dir() / "config" / "api_keys.json"


def _read_config() -> dict:
    try:
        p = _config_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_config(cfg: dict) -> bool:
    # indent=4 to match the file's existing shape, so a human opening it after
    # a code is issued does not find it reformatted.
    return safe_store.write_json(_config_path(), cfg, indent=4)


def _hash(code: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", code.encode("utf-8"), salt, _PBKDF2_ITERS).hex()


# ── the address codes are sent to ────────────────────────────────────

def recovery_email() -> str:
    return str(_read_config().get(_KEY_EMAIL, "") or "").strip()


def has_recovery_email() -> bool:
    return "@" in recovery_email()


def set_recovery_email(address: str) -> bool:
    cfg = _read_config()
    cfg[_KEY_EMAIL] = (address or "").strip()
    return _write_config(cfg)


def mask_email(address: str = None) -> str:
    """
    `someone@example.com` -> `s•••••e@example.com`.

    Shown instead of the address itself because the sign-in screen is visible
    to whoever is sitting at the machine, and the recovery address is exactly
    what an attacker would want to know.
    """
    addr = (address if address is not None else recovery_email()).strip()
    if "@" not in addr:
        return ""
    name, _, domain = addr.partition("@")
    if len(name) <= 2:
        hidden = "•" * len(name)
    else:
        hidden = name[0] + "•" * (len(name) - 2) + name[-1]
    return f"{hidden}@{domain}"


# ── rate limiting ────────────────────────────────────────────────────

def _recent_sends(cfg: dict) -> list[float]:
    raw = cfg.get(_KEY_SENDS, [])
    if not isinstance(raw, list):
        return []
    cutoff = time.time() - 3600
    out = []
    for t in raw:
        try:
            t = float(t)
        except (TypeError, ValueError):
            continue
        if t > cutoff:
            out.append(t)
    return out


def sends_remaining() -> int:
    return max(0, MAX_SENDS_PER_HOUR - len(_recent_sends(_read_config())))


def can_send() -> bool:
    return sends_remaining() > 0


def seconds_until_send_allowed() -> int:
    """How long until the hourly limit lets another code through."""
    recent = sorted(_recent_sends(_read_config()))
    if len(recent) < MAX_SENDS_PER_HOUR:
        return 0
    return max(0, int(recent[0] + 3600 - time.time()))


# ── issuing and checking ─────────────────────────────────────────────

def issue_code() -> str | None:
    """
    Make a new code, store its hash, and return the plaintext to be emailed.

    Returns None if the hourly limit is reached. The caller is expected to send
    the returned string immediately and then forget it — it is never written
    down anywhere, including here.
    """
    cfg = _read_config()
    recent = _recent_sends(cfg)
    if len(recent) >= MAX_SENDS_PER_HOUR:
        return None

    # secrets, not random: random's generator is predictable from its output,
    # which would make a code guessable from earlier ones.
    code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
    salt = os.urandom(32)

    cfg[_KEY_HASH] = _hash(code, salt)
    cfg[_KEY_SALT] = salt.hex()
    cfg[_KEY_EXPIRES] = time.time() + CODE_TTL_SECONDS
    recent.append(time.time())
    cfg[_KEY_SENDS] = recent
    if not _write_config(cfg):
        return None
    return code


def code_pending() -> bool:
    """Is there a live, unexpired code waiting to be typed in?"""
    cfg = _read_config()
    if not cfg.get(_KEY_HASH) or not cfg.get(_KEY_SALT):
        return False
    try:
        return float(cfg.get(_KEY_EXPIRES, 0)) > time.time()
    except (TypeError, ValueError):
        return False


def seconds_remaining() -> int:
    cfg = _read_config()
    try:
        return max(0, int(float(cfg.get(_KEY_EXPIRES, 0)) - time.time()))
    except (TypeError, ValueError):
        return 0


def clear_code() -> None:
    cfg = _read_config()
    for k in (_KEY_HASH, _KEY_SALT, _KEY_EXPIRES):
        cfg.pop(k, None)
    _write_config(cfg)


def verify_code(entered: str) -> bool:
    """
    True only for the current code, before it expires, once.

    Consumed on success so a code that has been read over someone's shoulder,
    or left sitting in an inbox, cannot be used a second time.
    """
    entered = (entered or "").strip().replace(" ", "")
    if not entered:
        return False

    cfg = _read_config()
    stored = cfg.get(_KEY_HASH, "")
    salt_hex = cfg.get(_KEY_SALT, "")
    if not stored or not salt_hex:
        return False

    try:
        if float(cfg.get(_KEY_EXPIRES, 0)) <= time.time():
            clear_code()
            return False
    except (TypeError, ValueError):
        clear_code()
        return False

    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False

    # compare_digest, not ==: a plain comparison returns faster on an early
    # mismatch, which leaks how much of the hash was right.
    ok = secrets.compare_digest(_hash(entered, salt), stored)
    if ok:
        clear_code()
    return ok
