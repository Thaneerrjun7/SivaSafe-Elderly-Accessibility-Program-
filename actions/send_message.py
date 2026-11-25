import json
import subprocess
import sys
import time
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.06
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

from config import get_base_dir as _get_base_dir
_base_dir = _get_base_dir

def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", "windows").lower()
    except Exception:
        return "windows"


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

def _open_app(app_name: str) -> bool:
    try:
        from actions.open_app import open_app
        result = open_app({"app_name": app_name})
        return "Could not" not in (result or "")
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open {app_name}: {e}")
        return False


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        time.sleep(4.0) 
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False

def _search_in_app(query: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")

    pyautogui.hotkey(*search_hotkey)
    time.sleep(0.5)
    _clear_and_paste(query)
    time.sleep(1.0)

def _desktop_send(app_name: str, receiver: str, message: str) -> str:
    if not _open_app(app_name):
        return f"Could not open {app_name}."

    time.sleep(1.0)
    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)
    return f"Message sent to {receiver} via {app_name}."

_GEMINI_CLIENT = None

def _wait_for_whatsapp_ready() -> bool:
    global _GEMINI_CLIENT
    print("[SendMessage] Checking if WhatsApp is ready...")
    try:
        import mss
        import mss.tools
        from google import genai
        from google.genai import types as gtypes
        
        if not _GEMINI_CLIENT:
            from config import get_api_key
            api_key = get_api_key()
            if not api_key:
                time.sleep(4.0)
                return True
            _GEMINI_CLIENT = genai.Client(api_key=api_key)
            
        for attempt in range(12):
            with mss.mss() as sct:
                target = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(target)
                png_bytes = mss.tools.to_png(shot.rgb, shot.size)
                
            prompt = "Look at this screen. Is the main WhatsApp chat interface fully loaded and ready (showing the chat list and a search bar)? Answer only YES or NO."
            
            response = _GEMINI_CLIENT.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    gtypes.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                    prompt
                ]
            )
            text = response.text.strip().lower()
            if "yes" in text:
                print("[SendMessage] WhatsApp seems ready.")
                return True
            else:
                print(f"[SendMessage] WhatsApp is not ready yet... waiting 2.5s. (Attempt {attempt+1}/12)")
                time.sleep(2.5)
                
        print("[SendMessage] WhatsApp failed to load within the timeout period.")
        return False
    except Exception as e:
        print(f"[SendMessage] Vision check error: {e}")
        time.sleep(5.0)
        return True            
        client = genai.Client(api_key=api_key)
        
        for attempt in range(5):
            with mss.mss() as sct:
                target = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(target)
                png_bytes = mss.tools.to_png(shot.rgb, shot.size)
                
            prompt = "Look at this screen. Is the main WhatsApp chat interface fully loaded and ready (showing the chat list and a search bar)? Answer only YES or NO."
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    gtypes.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                    prompt
                ]
            )
            text = response.text.strip().lower()
            if "yes" in text:
                print("[SendMessage] WhatsApp seems ready.")
                return True
            else:
                print(f"[SendMessage] WhatsApp is not ready yet... waiting 7s. (Attempt {attempt+1}/5)")
                time.sleep(7.0)
                
        print("[SendMessage] WhatsApp failed to load within the timeout period.")
        return False
    except Exception as e:
        print(f"[SendMessage] Vision check error: {e}")
        time.sleep(8.0)
        return True

def _send_whatsapp(receiver: str, message: str) -> str:
    if not _open_app("WhatsApp"):
        return "Could not open WhatsApp."

    if not _wait_for_whatsapp_ready():
        return "WhatsApp is unavailable at the moment. It did not finish loading (possibly due to network issues). Cannot send the message."

    time.sleep(1.0)
    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)
    return f"Message sent to {receiver} via WhatsApp."

def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("Telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("Signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("Discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram in browser."

    _paste_text(receiver)
    time.sleep(1.5)

    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")   
    time.sleep(0.4)

    time.sleep(2.0)
    screen_w, screen_h = pyautogui.size()
    pyautogui.click(screen_w // 2, screen_h - 150)
    time.sleep(0.5)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Instagram."


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return "Could not open Messenger in browser."


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Messenger."

_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                               _send_signal),
    ({"discord"},                              _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if any(k in key for k in keywords):
            return handler
    return lambda r, m: _desktop_send(platform_str.strip().title(), r, m)


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()

    if not receiver:
        return "Please specify a recipient."
    if not message_text:
        return "Please specify the message content."
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed — cannot control the desktop."

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")

    try:
        handler = _resolve_platform(platform)
        result  = handler(receiver, message_text)
    except Exception as e:
        result = f"Could not send message: {e}"

    print(f"[SendMessage] {'✅' if 'sent' in result.lower() else '❌'} {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result