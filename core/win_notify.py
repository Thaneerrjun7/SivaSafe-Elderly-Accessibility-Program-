"""
Who Windows thinks a SivaSafe notification came from.

Every Windows toast is shown under an AppUserModelID — the identity Windows
uses to pick the name and icon in the notification's header, and the entry it
files the app under in Settings › Notifications. Reminders used to borrow the
AppUserModelID belonging to powershell.exe, because PowerShell was what sent
them, so every reminder arrived captioned "Windows PowerShell".

An unpackaged desktop app can claim its own ID by writing it under
HKCU\\Software\\Classes\\AppUserModelId. No installer, no admin rights, and it
is confined to the current user's own hive. Windows reads DisplayName and
IconUri from there the first time a toast arrives under that ID.

Everything here is Windows-only and never raises: a reminder that cannot be
captioned should still be a reminder that fires.
"""

import sys
from pathlib import Path

AUMID = "SivaSafe.Assistant"
DISPLAY_NAME = "SivaSafe"

_REG_PATH = rf"Software\Classes\AppUserModelId\{AUMID}"

_registered = False


def _is_windows() -> bool:
    return sys.platform == "win32"


def icon_path() -> Path | None:
    """The app icon Windows should show on the toast, if it has been built."""
    p = Path(__file__).resolve().parent.parent / "assets" / "sivasafe.png"
    return p if p.exists() else None


def ensure_registered(force: bool = False) -> bool:
    """
    Claim the SivaSafe AppUserModelID for this user. Idempotent and cheap —
    safe to call on every launch. Returns True if the ID is registered.
    """
    global _registered
    if _registered and not force:
        return True
    if not _is_windows():
        return False

    try:
        import winreg
    except Exception:
        return False

    icon = icon_path()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _REG_PATH, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as key:
            # Rewriting only when it differs keeps launch quiet in RegMon and
            # avoids touching the hive on the overwhelmingly common no-op.
            def _put(name: str, value: str):
                try:
                    if winreg.QueryValueEx(key, name)[0] == value and not force:
                        return
                except FileNotFoundError:
                    pass
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

            _put("DisplayName", DISPLAY_NAME)
            if icon is not None:
                _put("IconUri", str(icon))
            # Matches the icon's own background, so Windows does not letterbox
            # it against a colour that clashes.
            _put("IconBackgroundColor", "FF376757")
        _registered = True
        return True
    except Exception as e:
        print(f"[Notify] Could not register the SivaSafe app id: {e}")
        return False


def _escape(text: str) -> str:
    """Toast payloads are XML — a stray & or < silently kills the whole toast."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def show_toast(title: str, message: str, *, sound: bool = True,
               linger: float = 0.0) -> bool:
    """
    Show a toast as SivaSafe. Returns True if Windows accepted it.

    duration="long" holds it on screen for about 25 seconds instead of the
    default 7 — the person this app is for should not have to catch it.

    `linger` holds the calling thread open for that many seconds afterwards,
    and a process whose only job was to show this toast should pass it. show()
    hands the toast to the Windows notification platform over COM and returns
    without waiting for it to land, so exiting immediately afterwards leaves
    delivery racing process teardown — with no error reported anywhere if it
    loses. The PowerShell script this replaced ended in `Start-Sleep 2` for
    the same reason. Leave it at zero when calling from the running app, where
    the process is not about to go anywhere and blocking would freeze the UI.
    """
    if not _is_windows():
        return False

    ensure_registered()

    try:
        from winrt.windows.ui.notifications import (
            ToastNotification, ToastNotificationManager,
        )
        from winrt.windows.data.xml.dom import XmlDocument
    except Exception as e:
        print(f"[Notify] Toast API unavailable: {e}")
        return False

    audio = ('<audio src="ms-winsoundevent:Notification.Reminder" />'
             if sound else '<audio silent="true" />')
    xml = (
        '<toast duration="long">'
        '<visual><binding template="ToastGeneric">'
        f'<text>{_escape(title)}</text>'
        f'<text>{_escape(message)}</text>'
        '</binding></visual>'
        f'{audio}'
        '</toast>'
    )

    try:
        doc = XmlDocument()
        doc.load_xml(xml)
        notifier = ToastNotificationManager.create_toast_notifier_with_id(AUMID)
        notifier.show(ToastNotification(doc))
        if linger > 0:
            import time
            time.sleep(linger)
        return True
    except Exception as e:
        print(f"[Notify] Toast failed: {e}")
        return False
