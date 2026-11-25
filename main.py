import sys
import os

# Force UTF-8 output on Windows terminals (cp1252 can't encode emoji/Unicode symbols)
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import asyncio
import re
import threading
import json
import traceback
from datetime import datetime
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from ui import SivaUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.plain_reader      import read_plain_language
from actions.gmail_navigator   import read_gmail_action
from actions.find_location     import location_action as find_location

# Guarded, unlike the rest. This module is the newest thing in the tree, and an
# import error anywhere in it would otherwise stop the whole assistant from
# starting rather than costing one capability.
try:
    from actions import content_safety
except Exception as _cs_err:                                # pragma: no cover
    content_safety = None
    print(f"[SIVA] ⚠️  content_safety unavailable: {_cs_err}")

# Guarded for the same reason. If the permission layer cannot be imported the
# assistant keeps every capability it had before it existed — the gate is there
# so nobody is surprised, and a gate that stops the app starting is a worse
# outcome than no gate at all.
try:
    from core import permissions
except Exception as _perm_err:                              # pragma: no cover
    permissions = None
    print(f"[SIVA] ⚠️  permissions unavailable: {_perm_err}")


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024


def _get_api_keys() -> tuple[str, str]:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
        return cfg.get("gemini_api_key", ""), cfg.get("gemini_api_key_secondary", "")


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are SIVA (SEE-vah), an advanced AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": (
            "Gives the weather report to user. Handles anywhere on Earth — a city, "
            "a country, a state or a region."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {
                    "type": "STRING",
                    "description": (
                        "Place to report on: city, country, state or region. "
                        "OMIT THIS ENTIRELY for the user's own location — never guess "
                        "a city for 'the weather' with no place named."
                    )
                }
            },
            # Not required. It used to be, which forced the model to invent a
            # city when the user just asked "what's the weather" — including
            # for the Weather quick-action button, whose whole meaning is here.
            "required": []
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                # Named explicitly. With only "The action to perform" to go on
                # the model invented plausible names — "reduce brightness" for
                # what the code calls brightness_down — and every one of those
                # came back "Unknown action", which reads to the user as the
                # assistant ignoring them. actions/computer_settings.py keeps
                # an alias table as a second line of defence.
                "action": {
                    "type": "STRING",
                    "description": (
                        "Exact action name. Use one of: brightness_up, "
                        "brightness_down, brightness_set (value 0-100), "
                        "volume_up, volume_down, volume_set (value 0-100), "
                        "mute, sleep_display, pause_video, close_app, "
                        "close_window, full_screen, minimize, maximize, "
                        "snap_left, snap_right, switch_window, show_desktop, "
                        "task_manager, focus_search, refresh_page, close_tab, "
                        "new_tab, next_tab, prev_tab, go_back, go_forward, "
                        "zoom_in, zoom_out, zoom_reset, find_on_page, "
                        "scroll_up, scroll_down, scroll_top, scroll_bottom, "
                        "page_up, page_down, copy, paste, cut, undo, redo, "
                        "select_all, save, enter, escape, screenshot, "
                        "lock_screen, open_settings, file_explorer, open_run, "
                        "dark_mode, toggle_wifi, type_text, press_key, "
                        "restart, shutdown."
                    ),
                },
                "description": {"type": "STRING", "description": "Natural language description of what to do, only if no exact action name fits"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, brightness 0-100, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, open, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | open | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home. For 'open', this is the folder the file is in — pass the folder shown by 'find', and the file name in 'name'."},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for, or to open"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
                "browser":     {"type": "STRING", "description": "For 'open' only. Name of the browser to open the file in, e.g. 'chrome'. Omit to open with the file's normal default program instead."},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },

    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },

    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_siva",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop SIVA. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "plain_reader",
        "description": (
            "Reads the screen or a specific file out loud in plain, simplified language. "
            "Use this when the user asks to 'read this email simply', 'read this document', "
            "or 'explain what is on my screen in plain language'. "
            "It will summarize and then read the content out loud using TTS."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path": {
                    "type": "STRING",
                    "description": "Optional path to a file to read. If empty, it captures the current screen."
                }
            }
        }
    },
    {
        "name": "gmail_navigator",
        "description": (
            "Autonomously navigates Gmail to search for and read an email out loud in plain language. "
            "Use this when the user asks to 'read my latest email from John' or 'check my email for project updates'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "search_query": {
                    "type": "STRING",
                    "description": "The search query to use in Gmail, e.g. 'from: John', 'subject: invoice', or 'is:unread'"
                }
            },
            "required": ["search_query"]
        }
    },
    {
        "name": "find_location",
        "description": "Finds the exact current location of the user natively using Wi-Fi geolocation. Use this when the user asks 'where am I?' or asks for their location.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "content_safety",
        "description": (
            "Checks whether a message, an email, a web address, a document or a "
            "picture is a scam, a phishing attempt, or content generated by AI. "
            "MUST be called whenever the user asks 'is this a scam', 'is this link "
            "safe', 'is this real', 'is this message genuine', 'did a computer make "
            "this picture', 'should I trust this', or shows you something that "
            "worries them. NEVER judge a message or a link yourself and never guess "
            "— always call this tool, because it checks the address and the wording "
            "against rules you cannot see. It returns a written verdict: read that "
            "verdict back to the user almost word for word, keep the reasons it "
            "gives, and always finish with its closing line about being a guide "
            "rather than a guarantee. Never tell the user something is definitely safe."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "The message, email text or web address to check, exactly as the user gave it.",
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Path to a picture or document to check. Leave empty to use the file the user has already attached.",
                },
                "source": {
                    "type": "STRING",
                    "description": "'text', 'file' or 'screen'. Use 'screen' when the suspicious thing is on the user's screen right now.",
                },
            },
            "required": [],
        }
    },
]

# --- Plugin system ---


class SivaLive:

    def __init__(self, ui: SivaUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._phone_active  = False   # True while phone mic is streaming; pauses PC mic
        self.ui.on_text_command  = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._was_online    = False   # has a live session connected before?
        
        import time
        self.last_activity_time = time.time()
        self.request_start_time = 0.0
        self.last_vad_time = 0.0

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    async def _monitor_status(self):
        import time
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            if self.request_start_time > 0 and (now - self.request_start_time) > 5.0:
                self.ui.set_state("CONNECTING")
            elif (now - self.last_activity_time) > 120.0:
                self.ui.set_state("SLEEPING")

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        import time
        self.last_activity_time = time.time()
        self.request_start_time = time.time()
        self.ui.set_state("PROCESSING")
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("MIC_ON")
        else:
            self.ui.set_state("MIC_OFF")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        import time
        self.last_activity_time = time.time()
        self.request_start_time = time.time()
        self.ui.set_state("PROCESSING")
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Aoede"
                    )
                )
            ),
        )

    # How long a permission card may sit unanswered before the tool gives up.
    # Slightly longer than SivaUI.request_permission's own wait, so the inner
    # timeout is the one that fires and returns cleanly rather than this one
    # cancelling a thread that is still holding the card open.
    _PERMISSION_WAIT = 125.0

    async def _permission_ok(self, name: str, args: dict) -> bool:
        """Whether this tool call is allowed to proceed — asking if need be.

        `run_in_executor`, never a bare blocking call: `_execute_tool` is
        awaited inside the receive loop, so anything that parks this coroutine
        without yielding takes the whole live session down with it. That is the
        same reasoning weather_report and content_safety already carry, and the
        stake is higher here because what it is waiting for is a person reading.
        """
        if permissions is None:
            return True
        try:
            key = permissions.for_tool(name, args)
            if not key or permissions.is_granted(key):
                return True
        except Exception as e:
            print(f"[SIVA] Permission check skipped for {name}: {e}")
            return True

        print(f"[SIVA] 🔐 asking permission: {key}")
        loop = asyncio.get_event_loop()
        try:
            return bool(await asyncio.wait_for(
                loop.run_in_executor(None, lambda: self.ui.request_permission(key)),
                timeout=self._PERMISSION_WAIT,
            ))
        except asyncio.TimeoutError:
            self.ui.write_log(f"SYS: Permission card for {key} was not answered.")
            return False
        except Exception as e:
            print(f"[SIVA] Permission ask failed for {key}: {e}")
            return True

    def _permission_refusal(self, name: str, args: dict) -> str:
        """What the model is told when the answer was no."""
        title = name
        try:
            perm = permissions.describe(permissions.for_tool(name, args))
            if perm is not None:
                title = perm.settings_line.rstrip(".").lower()
        except Exception:
            pass
        return (
            f"The user did not give permission for this ({title}), so it was not "
            "done. Tell them plainly, in one short sentence, that you need their "
            "permission for that and they can allow it next time they ask — or "
            "in Settings. Do not try it again and do not offer a workaround."
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[SIVA] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        # Before anything else, including save_memory — remembering things about
        # someone is one of the thirteen, and the early return below would sail
        # straight past a gate placed after it.
        if not await self._permission_ok(name, args):
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": self._permission_refusal(name, args)},
            )

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                # Hard ceiling. This coroutine is awaited inside the receive
                # loop, so a tool that never returns takes the whole session
                # down with it — which is precisely what a timeout-less HTTP
                # call to the geocoder used to do: SIVA simply stopped
                # answering. weather_action budgets itself to ~11s; this is the
                # backstop for anything it cannot foresee.
                try:
                    r = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, lambda: weather_action(parameters=args, player=self.ui)
                        ),
                        timeout=15.0,
                    )
                except asyncio.TimeoutError:
                    r = ("Sir, the weather service is not responding. "
                         "The connection may be down.")
                    self.ui.write_log(f"ERR: weather_report — timed out")
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."
                # Refresh the UI reminders display
                try:
                    self.ui.refresh_reminders()
                except Exception:
                    pass

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "web_search":
                self.ui.set_state("SEARCHING")
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "plain_reader":
                r = await loop.run_in_executor(None, lambda: read_plain_language(parameters=args, response=None, player=self.ui))
                result = r or "Started plain language reader."

            elif name == "content_safety":
                if content_safety is None:
                    result = "The safety checker is not available on this computer."
                else:
                    src = (args.get("source") or "").strip().lower()
                    text = (args.get("text") or "").strip()
                    path = (args.get("file_path") or "").strip()
                    if not path and not text and self.ui.current_file:
                        path = self.ui.current_file

                    if src == "screen" or (not text and not path):
                        png = await loop.run_in_executor(None, self.ui.capture_screen_full)
                        job = lambda: content_safety.check_image(png, kind="screen")
                    elif path:
                        job = lambda: content_safety.check_file(path)
                    else:
                        job = lambda: content_safety.check_text(text)

                    # Bounded, like the weather call above: this coroutine is
                    # awaited inside the receive loop, so a tool that never
                    # returns takes the whole live session down with it.
                    try:
                        verdict = await asyncio.wait_for(
                            loop.run_in_executor(None, job), timeout=45.0)
                    except asyncio.TimeoutError:
                        verdict = None
                    except Exception as e:
                        self.ui.write_log(f"ERR: content_safety — {e}")
                        verdict = None

                    if verdict is None:
                        result = ("The safety check did not finish. Tell the user plainly "
                                  "that it failed, and that they should not act on the "
                                  "message until someone they trust has looked at it.")
                    else:
                        try:
                            self.ui.show_safety_verdict(verdict)
                        except Exception:
                            pass          # the spoken answer still stands
                        result = verdict.spoken_summary()

            elif name == "gmail_navigator":
                try:
                    res = await loop.run_in_executor(None, lambda: read_gmail_action(args, self.ui, None))
                    return types.FunctionResponse(name=name, id=fc.id, response={"result": res})
                except Exception as e:
                    self.speak_error(name, e)
                    return types.FunctionResponse(name=name, id=fc.id, response={"error": str(e)})

            elif name == "find_location":
                try:
                    res = await loop.run_in_executor(None, lambda: find_location(args, self.ui, None))
                    return types.FunctionResponse(name=name, id=fc.id, response={"result": res})
                except Exception as e:
                    self.speak_error(name, e)
                    return types.FunctionResponse(name=name, id=fc.id, response={"error": str(e)})

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "shutdown_siva":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time
                    from core import shutdown as _sd
                    # Let "Goodbye, sir." finish, but never wait forever for
                    # it. The second loop below used to have no bound at all:
                    # if playback stalled or _is_speaking was never cleared,
                    # the app simply never closed and the only way out was
                    # Task Manager. A spoken farewell is a courtesy; quitting
                    # is not, so the courtesy is the part that gets a deadline.
                    deadline = time.time() + 12.0
                    start = time.time()
                    while (not self._is_speaking
                           and time.time() - start < 5.0
                           and time.time() < deadline):
                        time.sleep(0.1)
                    while self._is_speaking and time.time() < deadline:
                        time.sleep(0.2)
                    time.sleep(0.5)
                    # Same exit route as the window's X button.
                    _sd.shutdown(reason="shutdown_siva", ui=self.ui)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[SIVA] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[SIVA] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                siva_speaking = self._is_speaking
            if not siva_speaking and not self.ui.muted and not self._phone_active:
                import time
                import numpy as np
                data = indata.tobytes()
                
                audio_np = np.frombuffer(data, dtype=np.int16)
                rms = np.sqrt(np.mean(np.square(audio_np.astype(np.float32))))
                if rms > 300: # VAD threshold
                    if getattr(self, '_current_vad_state', '') != "LISTENING":
                        self.ui.set_state("LISTENING")
                        self._current_vad_state = "LISTENING"
                    self.last_vad_time = time.time()
                else:
                    if time.time() - getattr(self, 'last_vad_time', 0) > 1.0:
                        if getattr(self, '_current_vad_state', '') == "LISTENING":
                            self.ui.set_state("PROCESSING")
                            self._current_vad_state = "PROCESSING"
                            self.request_start_time = time.time()
                            
                self.last_activity_time = time.time()
                
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[SIVA] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[SIVA] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[SIVA] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():
                    import time
                    self.last_activity_time = time.time()
                    self.request_start_time = 0.0

                    if getattr(self, '_current_vad_state', '') == "PROCESSING":
                        if getattr(self.ui, '_muted', True):
                            self.ui.set_state("MIC_OFF")
                        else:
                            self.ui.set_state("MIC_ON")
                        self._current_vad_state = "MIC_ON"

                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"SIVA: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "siva",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[SIVA] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[SIVA] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[SIVA] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[SIVA] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _serve_dashboard(self) -> None:
        """
        Run the dashboard, and never let its failure take the assistant with it.

        uvicorn calls sys.exit(1) when it cannot bind its port — most often
        because an earlier copy of SivaSafe is still holding 8000. SystemExit
        is a BaseException, so a bare `create_task(serve())` does not contain
        it: asyncio re-raises it into the event loop and tears down the whole
        `asyncio.run()` — which is the loop the Gemini live session lives in.

        The symptom was brutal to diagnose because it was silent: the log
        showed "[SIVA] Connecting..." and never "Connected.", nothing appeared
        on screen, and the assistant was simply dead for that session — no
        voice, no tools, so no WhatsApp, no search, no reminders. Losing the
        phone dashboard is a missing feature; losing the assistant is the
        whole app.
        """
        try:
            await self._dashboard.serve()
        except SystemExit as e:
            from dashboard.server import PORT as _DASH_PORT
            print(f"[Dashboard] ✗ Could not start (exit {e.code}) — port "
                  f"{_DASH_PORT} is most likely held by another copy of "
                  f"SivaSafe that did not exit.", flush=True)
            print("[Dashboard] Remote Control is unavailable this session. "
                  "The assistant itself is unaffected.", flush=True)
            self._dashboard = None
        except Exception as e:
            print(f"[Dashboard] ✗ Stopped: {type(e).__name__}: {e}", flush=True)
            self._dashboard = None

    async def _process_dashboard_commands(self) -> None:
        while True:
            # serve() may have given up a moment after this task was created.
            if self._dashboard is None:
                return
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        from core.quota import KeyRotator, is_quota_error
        from config import get_live_api_keys

        keys = KeyRotator(get_live_api_keys())
        if not len(keys):
            print("[SIVA] ❌ No Gemini API key configured.")
            self.ui.write_log("SYS: No API key configured.")
        client = genai.Client(
            api_key=keys.current,
            http_options={"api_version": "v1beta"}
        )

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._serve_dashboard())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            try:
                print("[SIVA] Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    # Connected, so the whole ladder is worth trying again the
                    # next time one of them runs dry.
                    keys.note_success()
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    print("[SIVA] Connected.")
                    if getattr(self.ui, '_muted', True):
                        self.ui.set_state("MIC_OFF")
                    else:
                        self.ui.set_state("MIC_ON")
                    # One line per connection, saying which kind it was. The
                    # live session ends on its own — idle timeout, the API's
                    # own session limit, or a dropped network — and the loop
                    # below reconnects three seconds later. Writing the same
                    # "SIVA online." every time left the log claiming to have
                    # come online three times in a row with nothing in between
                    # to show that anything had gone wrong.
                    if self._was_online:
                        self.ui.write_log("SYS: Connection dropped — back online.")
                    else:
                        self.ui.write_log("SYS: SIVA online.")
                        self._was_online = True

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())
                    tg.create_task(self._monitor_status())

            except Exception as e:
                # Not `isinstance(e, APIError) and e.code == 429`. Over the
                # live websocket the SDK reports the socket's close code, not
                # an HTTP status, and a TaskGroup hands the failure over inside
                # an ExceptionGroup — so that test was false every time and the
                # spare key was never reached. See core/quota.py.
                if is_quota_error(e):
                    was = keys.where
                    if keys.rotate():
                        print(f"[SIVA] ⚠️ Quota exhausted on {was}. "
                              f"Rotating to {keys.where}…")
                        self.ui.write_log(
                            f"SYS: Quota exhausted — switching to {keys.where}.")
                        client = genai.Client(
                            api_key=keys.current,
                            http_options={"api_version": "v1beta"}
                        )
                        continue
                    print("[SIVA] ❌ All Gemini quotas exhausted.")
                    self.ui.write_log(
                        f"SYS: All {len(keys)} API keys are out of quota.")

                print(f"[SIVA] Error: {e}")
                traceback.print_exc()
            finally:
                self.session = None

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            print("[SIVA] Reconnecting in 3s...")
            await asyncio.sleep(3)

def main():
    # ── Keep running no matter what ───────────────────────────────────────────
    # Must be first: PyQt aborts the whole process when an exception escapes a
    # Qt event handler, and this assistant is meant to stay up all day.
    from core import crash_guard
    crash_guard.install()

    # ── No console windows flickering over the interface ──────────────────────
    # Also early, and for the same kind of reason: the firewall check runs
    # while the window is still opening, and every command it shells out to
    # would otherwise flash a black console on top of the app. Only visible
    # when launched the way a user launches it — from SivaSafe.exe, with no
    # terminal behind it — which is exactly when it matters. See core/proc.py.
    from core import proc
    proc.install()

    # ── Windows taskbar fix: give the app its own taskbar identity ────────────
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('SIVA.MarkXLVI.1.0')
        except Exception:
            pass

    # ── Single-window boot: MainWindow + auth overlay ─────────────────────────
    #
    # Instead of creating a separate auth QDialog that closes (causing a black
    # flash), we create the real MainWindow first and lay an opaque auth overlay
    # on top.  The user never sees a window close-and-reopen.  After auth
    # succeeds a cinematic "IDENTITY CONFIRMED" plays, the window smoothly
    # resizes from auth-size to full-size, and the overlay dissolves to reveal
    # the main dashboard underneath.

    # Build the main UI (creates QApplication, installs stylesheet, etc.)
    ui = SivaUI("face.png")

    from PyQt6.QtWidgets import QApplication as _QApp
    from PyQt6.QtCore import QTimer as _QTimer
    from core import prefs as _prefs
    from core.auth_overlay import (
        AuthOverlay, AUTH_WIDTH, AUTH_HEIGHT, AUTH_HEIGHT_PW,
    )

    auth_mode = _prefs.auth_mode()

    # Filled in below when there is an auth screen at all; a dict so the
    # closure can see the overlay that is created after this point.
    _overlay_ref = {"ov": None}

    def _send_to_password_reset():
        """Open Settings at the password box, with the reason on screen."""
        try:
            win = ui._win
            win._go_to_page("settings")
            page = win._centre_stack.widget(win._PAGE_MAP["settings"])
            if hasattr(page, "force_password_reset"):
                page.force_password_reset()
        except Exception as e:
            print(f"[SIVA] Could not open the password reset: {e}")

    def _prompt_for_recovery_email():
        try:
            ui._win.prompt_recovery_email_if_missing()
        except Exception as e:
            print(f"[SIVA] Could not ask for a recovery email: {e}")

    def _first_run_then_recovery():
        """The walkthrough if this is a first run, and only then the email ask.

        Chained rather than both fired on a timer, for the same reason the
        password rescue below suppresses the email prompt: two cards arriving
        over each other on somebody's very first launch is how a first
        impression becomes a thing to dismiss rather than a thing to read. If
        no tour is due, `maybe_start_walkthrough` runs the callback straight
        away and this is exactly what it was before.
        """
        try:
            ui._win.maybe_start_walkthrough(then=_prompt_for_recovery_email)
        except Exception as e:
            print(f"[SIVA] Walkthrough skipped: {e}")
            _prompt_for_recovery_email()

    def _on_auth_ready():
        """Signed in (or sign-in switched off) → start the SIVA backend."""
        ui._win.setWindowTitle("S.I.V.A.S.A.F.E — MARK 1")

        # A face scan let someone in who could not remember their password.
        # Take them to where they can set a new one — after a beat, so the
        # dashboard they just arrived at is what they see first.
        rescued = False
        try:
            if _overlay_ref.get("ov") is not None and _overlay_ref["ov"].needs_password_reset:
                rescued = True
                _QTimer.singleShot(1200, _send_to_password_reset)
        except Exception:
            pass

        # Every user must have a recovery email — it is the only way back in
        # once a password is truly forgotten. Asked for on every launch until
        # one exists. Skipped when a rescue is already sending them to
        # Settings, so they are not handed two things at once. On a first run
        # the walkthrough goes first and this waits behind it.
        if not rescued:
            _QTimer.singleShot(1500, _first_run_then_recovery)

        def reminder_maintenance():
            """
            Bring the OS scheduler in line with what the app believes.

            Three things, all of which talk to schtasks and so must stay off
            the UI thread, and none of which the person waits on:

              · claim the SivaSafe notification identity, so reminders arrive
                under our own name and icon rather than the sending program's
              · move reminders scheduled before the silent notifier existed
                over to it, so they stop flashing a PowerShell window
              · give every stored medication its recurring task, and clear
                away tasks left behind by ones since deleted or edited
            """
            try:
                from core.win_notify import ensure_registered
                ensure_registered()
            except Exception as e:
                print(f"[SIVA] Notification identity unavailable: {e}")

            try:
                from actions.reminder import migrate_scheduled_tasks
                migrate_scheduled_tasks()
            except Exception as e:
                print(f"[SIVA] Reminder migration skipped: {e}")

            try:
                from actions.med_schedule import resync_all
                made = resync_all()
                if made:
                    print(f"[SIVA] Scheduled {made} medication reminder(s).")
            except Exception as e:
                print(f"[SIVA] Medication scheduling skipped: {e}")

        threading.Thread(target=reminder_maintenance, daemon=True).start()

        def runner():
            import subprocess, sys, os
            try:
                exe = os.path.join(get_base_dir(), "scripts", "get_native_location.py")
                subprocess.Popen([sys.executable, exe])
            except:
                pass

            ui.wait_for_api_key()
            siva = SivaLive(ui)
            try:
                asyncio.run(siva.run())
            except KeyboardInterrupt:
                print("\n🔴 Shutting down...")

        threading.Thread(target=runner, daemon=True).start()

    # ── Sign-in switched off: no overlay, no camera, straight in ──────────
    #
    # The window is left at the size MainWindow chose for itself, because
    # there is no auth screen to grow out of. The backend start is deferred by
    # one turn of the event loop so the dashboard is on screen first — it is
    # the same ordering the reveal gives it, just without the cinematic.
    if auth_mode == _prefs.AUTH_OFF:
        print("[SIVA] Sign-in is switched off in Settings — opening directly.")
        _QTimer.singleShot(0, _on_auth_ready)
        ui.root.mainloop()
        return

    # Shrink the window to auth-appropriate size before the first paint.
    # MainWindow normally enforces a 1000×700 minimum, so relax it for auth.
    auth_h = AUTH_HEIGHT_PW if auth_mode == _prefs.AUTH_PASSWORD else AUTH_HEIGHT
    screen = _QApp.primaryScreen().availableGeometry()
    # Never taller than the screen it opens on. AuthOverlay's own transitions
    # already cap themselves at 94% of the available height; the first paint
    # went straight to the constant and would have hung off the bottom of a
    # short display. The preview absorbs whatever height is left.
    auth_h = min(auth_h, int(screen.height() * 0.94))
    ui._win.setMinimumSize(AUTH_WIDTH, 300)     # relax minimum for auth phase
    ui._win.resize(AUTH_WIDTH, auth_h)
    ui._win.move(
        screen.x() + (screen.width()  - AUTH_WIDTH) // 2,
        screen.y() + (screen.height() - auth_h)     // 2,
    )
    ui._win.setWindowTitle("S.I.V.A — AUTHENTICATION")

    # Place the auth overlay on top of the central widget
    overlay = AuthOverlay(
        parent=ui._win.centralWidget(),
        config_dir=BASE_DIR / "config",
        mode=auth_mode,
    )
    _overlay_ref["ov"] = overlay
    overlay.setGeometry(0, 0, ui._win.centralWidget().width(),
                        ui._win.centralWidget().height())
    overlay.raise_()

    # Install an event filter so the overlay stays full-size when the window
    # resizes (e.g. during the grow animation).
    from PyQt6.QtCore import QObject, QEvent
    class _ResizeWatcher(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Resize:
                try:
                    if overlay.isVisible():
                        overlay.setGeometry(0, 0, obj.width(), obj.height())
                except RuntimeError:
                    # The overlay deletes itself once the reveal is finished;
                    # every later window resize would otherwise raise on a
                    # dangling C++ object from inside an event filter.
                    obj.removeEventFilter(self)
            return False
    _watcher = _ResizeWatcher(ui._win)
    ui._win.centralWidget().installEventFilter(_watcher)

    def _on_auth_cancelled():
        print("[SIVA] 🔒 Authentication failed or cancelled.")
        sys.exit(0)

    overlay.ready.connect(_on_auth_ready)
    overlay.cancelled.connect(_on_auth_cancelled)
    overlay.begin()

    ui.root.mainloop()

if __name__ == "__main__":
    main()