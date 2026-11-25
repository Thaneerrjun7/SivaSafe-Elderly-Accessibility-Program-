"""
Moving on to the next API key when the one in use runs out.

Why this exists rather than a line of code at each call site
────────────────────────────────────────────────────────────
Both live sessions — the conversation in `main.py` and the vision session in
`actions/screen_processor.py` — used to decide they were out of quota like
this:

    if isinstance(e, APIError) and e.code == 429:

which never fired. Two reasons, and each is enough on its own:

1. **The live API does not report 429.** It is a websocket, and the SDK turns a
   closed socket into an APIError carrying the *websocket close code* rather
   than an HTTP status — `live.py` reads `e.rcvd.code` and hands it straight to
   `APIError.raise_error`. Those are 1000-range values: a rejected key comes
   back as 1007, a server-side stop as 1011. `raise_error` only produces a 429
   for a real HTTP 429, which is the REST path and not this one. Measured, not
   assumed: an invalid key over the live API raises
   `APIError(code=1007, 'API key not valid')`.

2. **The exception arrives wrapped.** Those sessions run under
   `asyncio.TaskGroup`, which collects failures into an ExceptionGroup, and
   `isinstance(group, APIError)` is False no matter what is inside it.

So the assistant would go quiet at the exact moment it was supposed to switch
keys, retry the exhausted key every three seconds, and never touch the spare.
Getting that wrong twice in two files is what makes it worth a module.
"""
from __future__ import annotations

from typing import Iterable, Sequence

# Matched against the text of the error, because the code cannot be trusted to
# say it (see above). The server's own wording travels in the websocket close
# reason, so "quota" and "RESOURCE_EXHAUSTED" both show up there in practice.
#
# Deliberately not matching a bare "429": over the live transport that number
# would have to appear in free text, and it is as likely to be part of a
# request id as a status.
_MARKERS = (
    "resource_exhausted",
    "resource exhausted",
    "quota",
    "rate limit",
    "rate-limit",
    "too many requests",
)


def is_quota_error(exc: BaseException) -> bool:
    """
    Is this the API saying *you have used your allowance*?

    Handles the exception however it arrives: on its own, inside an
    ExceptionGroup from a TaskGroup, or behind the `raise ... from` of a
    wrapper. Anything unrecognised is treated as not-quota, so an ordinary
    network drop reconnects on the same key instead of burning a spare.
    """
    for e in _flatten(exc):
        if getattr(e, "code", None) == 429:
            return True
        text = str(e).lower()
        if any(m in text for m in _MARKERS):
            return True
    return False


def _flatten(exc: BaseException, depth: int = 0) -> Iterable[BaseException]:
    """The exception and everything nested inside or behind it."""
    if exc is None or depth > 4:
        return
    yield exc
    for inner in getattr(exc, "exceptions", ()) or ():
        yield from _flatten(inner, depth + 1)
    for link in (exc.__cause__, exc.__context__):
        if link is not None and link is not exc:
            yield from _flatten(link, depth + 1)


class KeyRotator:
    """
    A ladder of API keys and a position on it.

    Rotation stops after one full lap rather than cycling forever: if every key
    has been tried since the last time one of them worked, they are all out,
    and continuing to swap would only hide that behind a reconnect loop. A
    later success resets the count, so a key that recovers the next day puts
    the whole ladder back in play.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        self._keys: list[str] = []
        for k in keys:
            k = (k or "").strip()
            # Deduplicated: the same key pasted into both slots is one key, and
            # rotating onto a copy of the exhausted key looks like a working
            # fallback in the log while changing nothing.
            if k and k not in self._keys:
                self._keys.append(k)
        self._i = 0
        self._tried = 0

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def current(self) -> str:
        return self._keys[self._i] if self._keys else ""

    @property
    def where(self) -> str:
        """'key 2 of 3' — for the log line the user actually sees."""
        if not self._keys:
            return "no key configured"
        return f"key {self._i + 1} of {len(self._keys)}"

    def note_success(self) -> None:
        """Called when a session connects, to put the whole ladder back."""
        self._tried = 0

    def rotate(self) -> bool:
        """
        Step to the next key. False when there is nowhere left to go — either
        there is only one key, or every one of them has been tried.
        """
        if len(self._keys) < 2:
            return False
        self._tried += 1
        if self._tried >= len(self._keys):
            return False
        self._i = (self._i + 1) % len(self._keys)
        return True
