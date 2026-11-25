"""
Sending SIVA's recovery mail.

Standard library only — `smtplib`, `ssl`, `email.message`. Nothing is added to
the dependency list for this, which matters on a machine where the app has to
keep working for years without anyone maintaining it.

Mail goes out through a **dedicated Gmail account belonging to SIVA**, using a
Gmail App Password. Two addresses are involved and they are not the same thing:

    smtp_sender        SIVA's own throwaway account — holds the App Password
    recovery_email     the user's real inbox — where codes actually arrive

Every function here returns ``(ok, message)`` and never raises. A mail server
being unreachable is an ordinary Tuesday; it must produce a sentence the user
can act on, not an exception that takes the sign-in screen down with it.

**This must never run on the GUI thread.** An SMTP connection can sit for
thirty seconds on a bad network, and the interface would be frozen for all of
it. Call it from a thread and deliver the result back with a signal — the
pattern `AuthGate._on_mic_click` already uses for the same reason.
"""

from __future__ import annotations

import json
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465          # implicit TLS
SMTP_TIMEOUT = 20        # seconds; a hung connection must not wait forever

_KEY_SENDER = "smtp_sender"
_KEY_APP_PASSWORD = "smtp_app_password"

# Deliberately loose. This catches "no @ at all" and "no dot in the domain",
# which is what users actually typo. Anything stricter starts rejecting real
# addresses, and the only test that truly counts is whether the mail arrives.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _read_config() -> dict:
    try:
        p = _base_dir() / "config" / "api_keys.json"
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def looks_like_email(address: str) -> bool:
    return bool(_EMAIL_RE.match((address or "").strip()))


def is_configured() -> bool:
    """Has SIVA been given a mail account to send from?"""
    cfg = _read_config()
    return bool(str(cfg.get(_KEY_SENDER, "")).strip()
                and str(cfg.get(_KEY_APP_PASSWORD, "")).strip())


def _send(to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    to_addr = (to_addr or "").strip()
    if not looks_like_email(to_addr):
        return False, "That does not look like an email address."

    cfg = _read_config()
    sender = str(cfg.get(_KEY_SENDER, "")).strip()
    # Gmail prints App Passwords in four groups of four; the spaces are for
    # reading and are not part of the password.
    app_pw = str(cfg.get(_KEY_APP_PASSWORD, "")).replace(" ", "").strip()

    if not sender or not app_pw:
        return False, ("SIVA has no email account set up yet. Add one in "
                       "Settings under Sign In Security.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"SIVA SAFE <{sender}>"
    msg["To"] = to_addr
    msg.set_content(body)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx,
                              timeout=SMTP_TIMEOUT) as server:
            server.login(sender, app_pw)
            server.send_message(msg)
        return True, f"Email sent to {to_addr}."

    except smtplib.SMTPAuthenticationError:
        return False, ("SIVA's own email account was rejected. Check the app "
                       "password in Settings — Gmail needs an App Password, "
                       "not the account's normal password.")
    except smtplib.SMTPRecipientsRefused:
        return False, f"{to_addr} was refused. Check the address is right."
    except (smtplib.SMTPException, ssl.SSLError) as e:
        return False, f"The mail server would not accept the message: {e}"
    except (TimeoutError, OSError):
        # Covers no internet, DNS failure, a blocked port and a dead router
        # alike — all the same thing as far as the user is concerned.
        return False, ("Could not reach the mail server. Check the internet "
                       "connection and try again.")
    except Exception as e:
        return False, f"Could not send the email: {e}"


def send_recovery_code(to_addr: str, code: str) -> tuple[bool, str]:
    """Send a sign-in code. Plain words, because of who is reading it."""
    body = (
        "Hello,\n\n"
        f"Your SIVA sign-in code is:  {code}\n\n"
        "Type this into SIVA to get back in. The code stops working in "
        "10 minutes, and it can only be used once.\n\n"
        "If you did not ask for this code, you can ignore this email — but "
        "somebody tried to sign in to SIVA on your computer.\n\n"
        "— S.I.V.A.S.A.F.E."
    )
    return _send(to_addr, "Your SIVA sign-in code", body)


def send_test_email(to_addr: str) -> tuple[bool, str]:
    """Prove the plumbing works before it is ever needed in anger."""
    body = (
        "Hello,\n\n"
        "This is a test message from SIVA. If you can read this, SIVA can "
        "reach you if you ever forget your password.\n\n"
        "There is nothing you need to do.\n\n"
        "— S.I.V.A.S.A.F.E."
    )
    return _send(to_addr, "SIVA test email", body)
