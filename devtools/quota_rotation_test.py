"""
Does the assistant actually move to the spare key when one runs out?

    python devtools/quota_rotation_test.py            # offline, instant
    python devtools/quota_rotation_test.py --live     # also calls the API

The offline part is the one that matters, because a quota error cannot be
summoned on demand: it feeds the detector the exact exception shapes the SDK
produces — including a real one captured from the live API — and walks a
rotator down a three-key ladder.

What it is guarding against is a fallback that reads correctly and never runs.
The previous version tested `isinstance(e, APIError) and e.code == 429`, which
is the REST shape; the live websocket reports close codes instead, so the
condition was false every time and the second key was never used. Nothing in
the tree noticed, because nothing exercised it.

`--live` additionally opens a real session on each configured key and says
which ones still have quota — the question worth asking before a demo.

Exit code is 0 when clean, 1 otherwise.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from google.genai import errors  # noqa: E402
from google.genai.errors import APIError  # noqa: E402

from core.quota import KeyRotator, is_quota_error  # noqa: E402


def _api_error(code: int, message: str) -> APIError:
    """An APIError shaped the way the SDK builds one."""
    try:
        errors.APIError.raise_error(code, message, None)
    except APIError as e:
        return e
    raise AssertionError("raise_error did not raise")


def _cases() -> list[tuple[str, BaseException, bool]]:
    """(description, exception, should this count as out-of-quota)."""
    live_quota = _api_error(1011, "close 1011: Quota exceeded for this key")
    rest_quota = _api_error(429, "RESOURCE_EXHAUSTED")
    bad_key = _api_error(1007, "API key not valid. Please pass a valid API key.")
    dropped = _api_error(1006, "Abnormal closure.")

    return [
        # The shapes that must rotate.
        ("live session closed on quota", live_quota, True),
        ("REST 429", rest_quota, True),
        ("wrapped in a TaskGroup's ExceptionGroup",
         ExceptionGroup("unhandled", [live_quota]), True),
        ("nested two groups deep",
         ExceptionGroup("outer", [ExceptionGroup("inner", [rest_quota])]), True),
        ("raised from, as a wrapper would",
         _chained(RuntimeError("session failed"), live_quota), True),
        ("RESOURCE_EXHAUSTED in the close reason",
         _api_error(1011, "RESOURCE_EXHAUSTED: allowance used"), True),

        # The shapes that must not — rotating here would spend a spare key on
        # a problem the spare key does not solve.
        ("an invalid key", bad_key, False),
        ("the network dropped", dropped, False),
        ("an ordinary bug", ValueError("something else went wrong"), False),
        ("a timeout", asyncio.TimeoutError(), False),
    ]


def _chained(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


def check_detector() -> list[str]:
    print("Detecting an out-of-quota error")
    bad: list[str] = []
    for name, exc, expected in _cases():
        got = is_quota_error(exc)
        mark = "ok " if got == expected else "FAIL"
        if got != expected:
            bad.append(f"{name}: expected {expected}, got {got}")
        print(f"  {mark}  {'rotates' if got else 'retries'}   {name}")
    print()
    return bad


def check_rotation() -> list[str]:
    print("Walking the ladder")
    bad: list[str] = []

    keys = KeyRotator(["AAA", "BBB", "CCC"])
    seen = [keys.current]
    while keys.rotate():
        seen.append(keys.current)
    print(f"  three keys      {' -> '.join(seen)}")
    if seen != ["AAA", "BBB", "CCC"]:
        bad.append(f"three-key ladder went {seen}")
    if keys.rotate():
        bad.append("kept rotating after every key had been tried")
    else:
        print("  then stops      rather than looping on exhausted keys")

    # A key that works again resets the ladder, so tomorrow's quota is usable
    # without restarting the app.
    keys.note_success()
    if not keys.rotate():
        bad.append("a later success did not put the ladder back in play")
    else:
        print("  after a success it is willing to rotate again")

    one = KeyRotator(["AAA"])
    if one.rotate():
        bad.append("rotated with only one key configured")
    else:
        print("  one key         nowhere to rotate to, as expected")

    same = KeyRotator(["AAA", "AAA", "  "])
    if len(same) != 1:
        bad.append(f"duplicate/blank keys not collapsed: {len(same)} kept")
    else:
        print("  the same key twice counts once")

    print()
    return bad


def check_live_keys() -> list[str]:
    """Open a real session on each configured key. Keys are never printed."""
    from google import genai
    from google.genai import types as gtypes

    from config import get_live_api_keys

    model = "models/gemini-2.5-flash-native-audio-preview-12-2025"
    print(f"Live check against {model}")

    async def one(key: str) -> str:
        client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
        cfg = gtypes.LiveConnectConfig(response_modalities=["AUDIO"])
        try:
            async with client.aio.live.connect(model=model, config=cfg):
                return "quota available"
        except BaseException as e:  # noqa: BLE001
            return ("OUT OF QUOTA" if is_quota_error(e)
                    else f"failed: {str(e).replace(key, '<key>')[:70]}")

    async def run() -> None:
        for i, key in enumerate(get_live_api_keys(), start=1):
            print(f"  key {i} ...{key[-4:]}   {await one(key)}")

    asyncio.run(run())
    print()
    return []


def main() -> int:
    problems = check_detector() + check_rotation()
    if "--live" in sys.argv:
        problems += check_live_keys()

    if problems:
        print(f"FAILED — {len(problems)} problems")
        for p in problems:
            print(f"  {p}")
        return 1
    print("PASS — quota errors are recognised and the spare key is reached")
    return 0


if __name__ == "__main__":
    sys.exit(main())
