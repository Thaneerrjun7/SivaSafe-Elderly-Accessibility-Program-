# config/__init__.py
import json, os, platform
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "api_keys.json"

def _platform_os() -> str:
    """Auto-detect OS when config file is absent."""
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def get_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", _platform_os()).lower()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"

def get_base_dir() -> Path:
    return Path(__file__).parent.parent

def get_api_key() -> str:
    return get_config().get("gemini_api_key", "")

def get_secondary_api_key() -> str:
    return get_config().get("gemini_api_key_secondary", "")

def get_tertiary_api_key() -> str:
    return get_config().get("gemini_api_key_tertiary", "")

def get_live_api_keys() -> list[str]:
    """Key ladder for the conversation — primary, then the spares.

    Primary first, which is the opposite of the vision ladder below and for the
    same reason: the two sessions are kept off each other's allowance. This is
    the one the user is talking to, so it gets the key they think of as theirs
    and falls back only when that runs out.

    Deduplicated, and empty slots dropped. With only a primary configured this
    returns a single-item list and the caller simply never rotates.
    """
    out, seen = [], set()
    for k in (get_api_key(), get_secondary_api_key(), get_tertiary_api_key()):
        k = (k or "").strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def get_vision_api_keys() -> list[str]:
    """Key ladder for the heavy vision and safety work.

    The free tier allows five requests a minute *per key*, and looking at a
    screen or screening a picture is expensive next to a sentence of
    conversation. Sharing one key meant a safety check could use up the
    allowance the conversation was about to need, and the user would hear the
    assistant go quiet for no reason they could see. A third key keeps the two
    apart.

    Ordered tertiary first, then the existing pair, deduplicated. With no
    tertiary key configured this returns exactly [primary, secondary] — the
    behaviour before it existed — so nothing has to be set up for the app to
    keep working.
    """
    out, seen = [], set()
    for k in (get_tertiary_api_key(), get_api_key(), get_secondary_api_key()):
        k = (k or "").strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out
