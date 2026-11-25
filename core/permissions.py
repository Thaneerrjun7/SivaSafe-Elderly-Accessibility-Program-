"""
What SIVA is allowed to do — asked once, in plain words, then remembered.

Before this existed the app opened the camera, listened on the microphone,
opened WhatsApp, photographed the screen and drove the computer with no consent
step anywhere. For software aimed at an older adult that is the wrong default:
the person should be told what is about to happen, in words they do not have to
decode, and be able to say no.

Thirteen named capabilities, each with its own wording. The first time one is
needed a card appears with two buttons, Allow and Cancel.

**Allow is stored. Cancel is not.** That asymmetry is deliberate. A stored "no"
means one mis-click permanently disables a feature, on a machine whose owner is
unlikely to go hunting through Settings to work out why the WhatsApp button
stopped doing anything. An unstored "no" costs one extra card the next time they
genuinely want the feature, which is the cheaper mistake by a long way. Both
directions are reversible from Settings either way.

No Qt in here, and no global state — it is a store and a table of strings, so
the wording and the tool mapping can be tested without launching the app. Same
shape as ``actions/content_safety.py``.
"""

from __future__ import annotations

from core import prefs

# ── the preference keys ──────────────────────────────────────────────
#
# One key holding a list, overwritten in place, exactly like
# ``text_scale_level``. Denials are never written, so nothing accumulates: the
# list can only ever be as long as the table below.

_STORE_KEY = "permissions"
_TOUR_KEY = "walkthrough_done"


# ── the thirteen ─────────────────────────────────────────────────────

CAMERA = "camera"
MICROPHONE = "microphone"
AUTOMATION = "automation"
SCREEN = "screen"
WHATSAPP = "whatsapp"
EMAIL = "email"
WEB = "web"
APPS = "apps"
FILES = "files"
SYSTEM = "system"
LOCATION = "location"
REMINDERS = "reminders"
MEMORY = "memory"


class Permission:
    """One capability, and everything the interface needs to describe it.

    ``body`` always says what SIVA will *not* do as well as what it will. That
    is the same rule ``content_safety.Finding.why`` follows, and for the same
    reason: a permission prompt that only lists powers reads as a threat, and
    the honest limit is usually the part that decides the answer.
    """

    __slots__ = ("key", "icon", "name", "title", "body", "settings_line")

    def __init__(self, key: str, icon: str, name: str, title: str, body: str,
                 settings_line: str):
        self.key = key
        self.icon = icon
        self.name = name                 # short label, for the Settings list
        self.title = title               # the question, for the card
        self.body = body
        self.settings_line = settings_line


FOOTNOTE = ("You will only be asked this once. "
            "You can change it later in Settings.")


PERMISSIONS: dict[str, Permission] = {
    p.key: p for p in (
        Permission(
            CAMERA, "📷", "The camera",
            "Let SIVA use the camera?",
            "SIVA uses the camera to look at things you hold up to it, and to "
            "check a photo you are unsure about. It does not record video, and "
            "nothing is kept once it has answered you.",
            "Look at things you hold up to the camera.",
        ),
        Permission(
            MICROPHONE, "🎙", "Your voice",
            "Let SIVA listen when you talk?",
            "This is how you speak to SIVA instead of typing. It only listens "
            "while you hold the Talk button, or while Listen is switched on — "
            "never on its own.",
            "Hear you when you press Talk or Listen.",
        ),
        Permission(
            AUTOMATION, "🖱", "Doing things on this computer",
            "Let SIVA do things on this computer?",
            "This is what lets SIVA open programs and click and type for you. "
            "It only ever acts on something you have just asked it to do.",
            "Click and type for you when you ask.",
        ),
        Permission(
            SCREEN, "👁", "Looking at your screen",
            "Let SIVA look at your screen?",
            "SIVA takes one picture of what is on your screen and reads it "
            "back to you. The picture goes to Google's AI to be described, and "
            "is not kept on this computer.",
            "Take a picture of your screen and read it out.",
        ),
        Permission(
            WHATSAPP, "💬", "WhatsApp",
            "Let SIVA open WhatsApp?",
            "SIVA will open WhatsApp and can type out a message you dictate to "
            "it. It cannot read your old conversations, and it never sends "
            "anything you have not asked it to send.",
            "Open WhatsApp and type a message you dictate.",
        ),
        Permission(
            EMAIL, "📧", "Your email",
            "Let SIVA open your email?",
            "SIVA can open Gmail and read a message aloud to you. It does not "
            "reply to anyone, and it does not delete anything.",
            "Open your email and read a message aloud.",
        ),
        Permission(
            WEB, "🌐", "Websites and searching",
            "Let SIVA open websites and search the internet?",
            "SIVA can look things up for you and open a page in your browser. "
            "It will not sign in to anything as you, and it will not buy "
            "anything.",
            "Search the internet and open web pages.",
        ),
        Permission(
            APPS, "📂", "Opening programs",
            "Let SIVA open programs on this computer?",
            "So you can say \"open my photos\" instead of hunting for it. It "
            "only opens programs that are already installed — it cannot "
            "install anything new.",
            "Open programs that are already on this computer.",
        ),
        Permission(
            FILES, "📄", "Your files",
            "Let SIVA open your files?",
            "SIVA can open a document you point it at and read it out, or "
            "explain it in simpler words. It does not delete or change your "
            "files.",
            "Open and read out a document you choose.",
        ),
        Permission(
            SYSTEM, "⚙", "This computer's settings",
            "Let SIVA change this computer's settings?",
            "Things like the volume, the screen brightness and the Wi-Fi. It "
            "cannot change your password, and it cannot touch anyone else's "
            "account.",
            "Change the volume, brightness and Wi-Fi.",
        ),
        Permission(
            LOCATION, "📍", "Which town you are in",
            "Let SIVA know which town you are in?",
            "Used for the weather, and for finding places near you. It is the "
            "town, not your address, and it is not shared with anyone.",
            "Use your town for weather and nearby places.",
        ),
        Permission(
            REMINDERS, "⏰", "Reminders and alerts",
            "Let SIVA set reminders and alerts?",
            "SIVA hands your reminders to Windows so they still arrive even if "
            "SIVA is closed. You can delete any of them from the Reminders "
            "page.",
            "Set reminders that arrive even when SIVA is closed.",
        ),
        Permission(
            MEMORY, "🧠", "Remembering things about you",
            "Let SIVA remember things about you?",
            "Names, and how you like things done, so you do not have to say "
            "them twice. It is kept on this computer only, and Settings can "
            "erase all of it.",
            "Remember names and preferences between conversations.",
        ),
    )
}

# Display order for the Settings list: the three a person is asked for up front,
# then the rest roughly by how often they come up.
ORDER = (CAMERA, MICROPHONE, AUTOMATION, SCREEN, WHATSAPP, EMAIL, WEB,
         APPS, FILES, SYSTEM, LOCATION, REMINDERS, MEMORY)


def describe(key: str) -> Permission | None:
    return PERMISSIONS.get(key)


# ── the store ────────────────────────────────────────────────────────

def granted_keys() -> set[str]:
    """Everything allowed so far. Never raises; unknown keys are dropped."""
    try:
        raw = prefs.get(_STORE_KEY, [])
        if not isinstance(raw, list):
            return set()
        return {k for k in raw if k in PERMISSIONS}
    except Exception:
        return set()


def is_granted(key: str) -> bool:
    """
    Whether this capability is allowed.

    **Anything that goes wrong reads as allowed**, and that is the opposite of
    the rule ``prefs.auth_mode()`` follows. The difference is what each one is
    protecting. A damaged preferences file must not be able to switch the
    sign-in off, so that one fails closed. This gate exists so nobody is
    surprised by what the app does — not to keep an attacker out, since anyone
    who can rewrite the file can also run the underlying action directly. Failing
    closed here would mean a truncated write leaves an assistant that refuses
    every request with no explanation, which is a far worse day than one extra
    card.
    """
    if key not in PERMISSIONS:
        return True                      # nothing to ask about
    try:
        return key in granted_keys()
    except Exception:
        return True


def grant(key: str) -> bool:
    if key not in PERMISSIONS:
        return False
    current = granted_keys()
    if key in current:
        return True
    current.add(key)
    return prefs.set(_STORE_KEY, sorted(current))


def revoke(key: str) -> bool:
    current = granted_keys()
    if key not in current:
        return True
    current.discard(key)
    return prefs.set(_STORE_KEY, sorted(current))


def reset_all() -> bool:
    """Forget every answer — what the demo button in Settings calls."""
    return prefs.set(_STORE_KEY, [])


# ── the walkthrough flag ─────────────────────────────────────────────

def walkthrough_done() -> bool:
    try:
        return bool(prefs.get(_TOUR_KEY, False))
    except Exception:
        return True                      # never trap someone in a tour


def set_walkthrough_done(done: bool = True) -> bool:
    return prefs.set(_TOUR_KEY, bool(done))


# ── which permission a tool needs ────────────────────────────────────
#
# Keyed by the tool names declared in main.py's TOOL_DECLARATIONS. Two of them
# cannot be answered from the name alone — open_app and send_message both depend
# on which app they were pointed at, which is the whole reason WhatsApp is its
# own permission rather than being folded into "open programs".

_SIMPLE: dict[str, str] = {
    "gmail_navigator":   EMAIL,
    "browser_control":   WEB,
    "web_search":        WEB,
    "youtube_video":     WEB,
    "flight_finder":     WEB,
    "screen_process":    SCREEN,
    "computer_settings": SYSTEM,
    "computer_control":  SYSTEM,
    "desktop_control":   AUTOMATION,
    "file_controller":   FILES,
    "file_processor":    FILES,
    "plain_reader":      FILES,
    "find_location":     LOCATION,
    "weather_report":    LOCATION,
    "reminder":          REMINDERS,
    "save_memory":       MEMORY,
}

# Not gated, on purpose:
#   shutdown_siva — the way out is never allowed to need permission.
_UNGATED = frozenset({"shutdown_siva"})

# Matched against send_message's `platform` argument. The keyword sets mirror
# _PLATFORM_MAP in actions/send_message.py; anything it does not recognise falls
# through to a generic desktop send, which is app automation.
_MESSAGING = (
    ({"whatsapp", "wp", "wapp"}, WHATSAPP),
    ({"mail", "email", "gmail", "outlook"}, EMAIL),
)


def for_tool(name: str, args: dict | None = None) -> str | None:
    """The permission a tool call needs, or None if it needs none."""
    args = args or {}

    if name in _UNGATED:
        return None

    if name in _SIMPLE:
        return _SIMPLE[name]

    if name == "open_app":
        target = str(args.get("app_name") or "").lower()
        return WHATSAPP if "whatsapp" in target else APPS

    if name == "send_message":
        platform = str(args.get("platform") or "whatsapp").lower().strip()
        for keywords, key in _MESSAGING:
            if any(k in platform for k in keywords):
                return key
        return APPS

    if name == "content_safety":
        # Checking text the user pasted in needs nothing — they typed it, and it
        # goes no further than the check itself. Reading the screen or opening a
        # file does, and is the same act as doing either of those directly.
        source = str(args.get("source") or "").lower().strip()
        if source == "screen":
            return SCREEN
        if args.get("file_path"):
            return FILES
        if args.get("text"):
            return None
        return SCREEN                    # no argument at all means "this screen"

    return None
