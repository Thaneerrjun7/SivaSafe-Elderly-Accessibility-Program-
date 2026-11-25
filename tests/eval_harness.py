#!/usr/bin/env python3
"""
eval_harness.py -- SIVA Evaluation Harness
==========================================
Evaluation 1: Tool-selection accuracy (Gemini function-calling, no side effects)
Evaluation 2: Action-layer execution (read-only / reversible calls only)

Results written to eval_results.json in the Logbook and Final Report folder.
"""

import sys
import os

# Force UTF-8 output on Windows terminals (cp1252 can't encode emoji/Unicode)
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import csv
import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Bootstrap — make sure project root is on sys.path so we can import actions/
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CSV_PATH = Path(os.environ.get("SIVA_EVAL_CSV", PROJECT_ROOT / "tests" / "task_success_sheet.csv"))
OUTPUT_PATH = Path(os.environ.get("SIVA_EVAL_OUTPUT", PROJECT_ROOT / "tests" / "eval_results.json"))
PROMPT_PATH = PROJECT_ROOT / "core" / "prompt.txt"
API_CONFIG_PATH = PROJECT_ROOT / "config" / "api_keys.json"

RATE_LIMIT_SLEEP = 13  # seconds between Gemini calls (free tier: 5 RPM)

# ---------------------------------------------------------------------------
# The 20 tool declarations (extracted verbatim from main.py).
#
# TODO: this is a copy and will drift. Importing TOOL_DECLARATIONS from main.py
# is the real fix, but main.py imports ui, which pulls in a QApplication-adjacent
# chain that has no business running inside a routing test.
# ---------------------------------------------------------------------------
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
            # Kept in step with main.py's declaration; the eval is only
            # meaningful while the two match.
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
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
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
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
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

# ---------------------------------------------------------------------------
# Expected tool mapping — manually curated for the 24 CSV rows
# Each key pattern in the command maps to the most appropriate tool.
# We also accept valid alternate tools where the model's choice is defensible.
# ---------------------------------------------------------------------------
EXPECTED_TOOL_MAP = {
    "Open File Explorer":                                  {"primary": "open_app",          "alternates": []},
    "Set the volume to 50 percent":                        {"primary": "computer_settings", "alternates": []},
    "Take a screenshot":                                   {"primary": "computer_settings", "alternates": ["computer_control"]},
    "Minimise all windows":                                {"primary": "computer_settings", "alternates": ["computer_control"]},
    "Open Notepad":                                        {"primary": "open_app",          "alternates": []},
    "Find a file called <pick one you have>":              {"primary": "file_controller",   "alternates": []},
    "Read this document in plain language":                {"primary": "plain_reader",       "alternates": []},
    "Summarise what is on my screen":                      {"primary": "screen_process",     "alternates": []},
    "Search the web for the weather in Kuala Lumpur":      {"primary": "web_search",         "alternates": ["weather_report"]},
    "Open google.com in Chrome":                           {"primary": "browser_control",    "alternates": ["open_app"]},
    "Search YouTube for relaxing music":                   {"primary": "youtube_video",      "alternates": ["browser_control"]},
    "Read my latest email":                                {"primary": "gmail_navigator",    "alternates": ["plain_reader"]},
    "Check my email for anything from <a real sender>":    {"primary": "gmail_navigator",    "alternates": []},
    "Send a WhatsApp message to <a real contact>":         {"primary": "send_message",       "alternates": []},
    "Remind me to take my tablets at 8 pm":                {"primary": "reminder",           "alternates": []},
    "Add paracetamol to my medications":                   {"primary": "save_memory",        "alternates": []},
    "What reminders do I have today?":                     {"primary": "reminder",           "alternates": ["web_search"]},
    "Remember that my daughter is called <name>":          {"primary": "save_memory",        "alternates": []},
    "What did I just tell you about my daughter?":         {"primary": "save_memory",        "alternates": ["web_search"]},
    "What is the weather today?":                          {"primary": "weather_report",     "alternates": ["web_search"]},
    "Where am I right now?":                               {"primary": "find_location",      "alternates": []},
    # Both of these used to expect web_search, from before there was anything
    # that could actually answer them. They are the two rows the content_safety
    # tool exists for.
    "Is this link safe: <paste a suspicious-looking URL>": {"primary": "content_safety",  "alternates": ["web_search"]},
    "Does this message look like a scam? <paste sample text>": {"primary": "content_safety", "alternates": []},
    "Was this picture made by AI?":                        {"primary": "content_safety",  "alternates": ["screen_process"]},
    "Show me my heart rate":                               {"primary": "web_search",         "alternates": ["screen_process", "computer_control"]},
}


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are SIVA (SEE-vah), an advanced AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks."
        )


def _get_api_keys() -> list[str]:
    """Return both primary and secondary Gemini API keys."""
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    keys = []
    for k in ("gemini_api_key", "gemini_api_key_secondary"):
        v = cfg.get(k, "").strip()
        if v:
            keys.append(v)
    return keys


# ===================================================================
#  EVALUATION 1 — Tool-selection accuracy
# ===================================================================

def _call_gemini_with_retry(clients, system_prompt, command, max_retries=3):
    """
    Try each client in order. On 429, wait the suggested retry delay
    (or 60s default) and try the next key.  Returns (chosen_tool, chosen_args, error).
    """
    from google.genai import types
    from google.genai.errors import APIError

    for attempt in range(max_retries):
        for ci, client in enumerate(clients):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=command,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        tools=[{"function_declarations": TOOL_DECLARATIONS}],
                        tool_config=types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(
                                mode="ANY"
                            )
                        ),
                        temperature=0.0,
                    ),
                )

                # Extract function call
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        if part.function_call:
                            return part.function_call.name, dict(part.function_call.args or {}), None
                return None, {}, "No function call in response"

            except APIError as e:
                err_code = getattr(e, 'code', None) or getattr(e, 'status_code', None) or 0
                if err_code == 429 or '429' in str(e):
                    # Extract retry delay from error message
                    import re
                    delay_match = re.search(r'retry in ([\d.]+)s', str(e))
                    wait_time = float(delay_match.group(1)) + 2 if delay_match else 60
                    key_label = f"Key-{ci+1}"
                    print(f"\n    [429 on {key_label}, attempt {attempt+1}] waiting {wait_time:.0f}s...", end=" ", flush=True)
                    time.sleep(wait_time)
                    continue
                else:
                    return None, {}, str(e)
            except Exception as e:
                if '429' in str(e):
                    import re
                    delay_match = re.search(r'retry in ([\d.]+)s', str(e))
                    wait_time = float(delay_match.group(1)) + 2 if delay_match else 60
                    key_label = f"Key-{ci+1}"
                    print(f"\n    [429 on {key_label}, attempt {attempt+1}] waiting {wait_time:.0f}s...", end=" ", flush=True)
                    time.sleep(wait_time)
                    continue
                return None, {}, str(e)

    return None, {}, "All API keys exhausted after retries"


def eval_tool_selection() -> list[dict]:
    """
    For each command in the CSV, send it to gemini-2.5-flash with the
    full tool registry.  Record which tool the model selected.
    Uses both primary and secondary API keys with retry on 429.
    """
    from google import genai

    api_keys = _get_api_keys()
    if not api_keys:
        print("ERROR: No Gemini API keys found in config/api_keys.json")
        sys.exit(1)

    clients = [genai.Client(api_key=k) for k in api_keys]
    print(f"  Using {len(clients)} API key(s) with round-robin fallback\n")

    # Build system prompt with time context
    now = datetime.now()
    time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
    time_ctx = (
        f"[CURRENT DATE & TIME]\n"
        f"Right now it is: {time_str}\n"
        f"Use this to calculate exact times for reminders.\n\n"
    )
    system_prompt = time_ctx + _load_system_prompt()

    # Read CSV
    rows = []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cmd = row.get("COMMAND", "").strip()
            domain = row.get("DOMAIN", "").strip()
            if cmd:
                rows.append({"domain": domain, "command": cmd})

    print(f"\n{'='*72}")
    print(f"  EVALUATION 1 — Tool-Selection Accuracy  ({len(rows)} commands)")
    print(f"{'='*72}\n")

    results = []
    for i, row in enumerate(rows):
        command = row["command"]
        domain = row["domain"]

        print(f"  [{i+1:2d}/{len(rows)}] {command[:60]:<60s}", end=" ", flush=True)

        # Rate-limit: sleep between calls (except first)
        if i > 0:
            time.sleep(RATE_LIMIT_SLEEP)

        t0 = time.perf_counter()

        # Rotate which client goes first to spread load
        rotated = clients[i % len(clients):] + clients[:i % len(clients)]
        chosen_tool, chosen_args, error = _call_gemini_with_retry(rotated, system_prompt, command)

        duration = time.perf_counter() - t0

        # Determine correctness
        expected_entry = EXPECTED_TOOL_MAP.get(command)
        if expected_entry:
            expected_tool = expected_entry["primary"]
            alternates = expected_entry["alternates"]
            tool_correct = (chosen_tool == expected_tool) or (chosen_tool in alternates)
        else:
            expected_tool = "UNKNOWN"
            tool_correct = None

        status_icon = "✅" if tool_correct else ("⚠️ " if tool_correct is None else "❌")
        print(f"→ {chosen_tool or 'NONE':<22s} {status_icon}  ({duration:.1f}s)")

        results.append({
            "domain":           domain,
            "command":          command,
            "chosen_tool":      chosen_tool,
            "chosen_args":      chosen_args,
            "expected_tool":    expected_tool,
            "tool_correct":     tool_correct,
            "execution_status": "PASS" if not error else "FAIL",
            "duration_s":       round(duration, 3),
            "error":            error,
        })

    return results


# ===================================================================
#  EVALUATION 2 — Action-layer execution
# ===================================================================

# Tools to skip entirely (irreversible / sends real messages)
SKIP_TOOLS = {
    "send_message",       # sends real WhatsApp messages
    "shutdown_siva",      # kills the process
    "file_controller",    # delete action could destroy data; skip entirely for safety
    "screen_process",     # requires live camera/screen capture session + audio
    "browser_control",    # launches browser sessions with Playwright
    "gmail_navigator",    # opens Chrome, navigates Gmail
    "computer_control",   # clicks, types on real screen — can cause side effects
    "plain_reader",       # captures screen + calls Gemini API with TTS audio
    "file_processor",     # can modify/convert files
    "flight_finder",      # uses Playwright to scrape Google Flights
}

# Safe test configs for each callable action module
ACTION_TEST_CONFIGS = {
    "open_app": {
        "module":   "actions.open_app",
        "function": "open_app",
        "args":     {"parameters": {"app_name": "notepad"}},
        "description": "Opens Notepad (harmless, can be closed)",
    },
    "weather_report": {
        "module":   "actions.weather_report",
        "function": "weather_action",
        "args":     {"parameters": {"city": "London"}},
        "description": "Fetches weather for London via free API (read-only)",
    },
    "web_search": {
        "module":   "actions.web_search",
        "function": "web_search",
        "args":     {"parameters": {"query": "current time in London"}},
        "description": "Web search (read-only API call)",
    },
    "reminder": {
        "module":   "actions.reminder",
        "function": "reminder",
        "args":     {"parameters": {"date": "2099-12-31", "time": "23:59", "message": "SIVA eval test reminder"}},
        "description": "Sets a far-future test reminder (reversible via Task Scheduler)",
    },
    "youtube_video": {
        "module":   "actions.youtube_video",
        "function": "youtube_video",
        "args":     {"parameters": {"action": "trending", "region": "US"}},
        "description": "Fetches trending videos (read-only scrape)",
    },
    "computer_settings": {
        "module":   "actions.computer_settings",
        "function": "computer_settings",
        "args":     {"parameters": {"action": "volume", "value": "50"}},
        "description": "Sets volume to 50% (easily reversible)",
    },
    "desktop_control": {
        "module":   "actions.desktop",
        "function": "desktop_control",
        "args":     {"parameters": {"action": "list"}},
        "description": "Lists desktop files (read-only)",
    },
    "find_location": {
        "module":   "actions.find_location",
        "function": "location_action",
        "args":     {"parameters": {}},
        "description": "Reads cached location data (read-only)",
    },
    "save_memory": {
        # save_memory is handled in main.py, not a standalone module.
        # We test the underlying memory_manager directly.
        "module":   "memory.memory_manager",
        "function": "load_memory",
        "args":     {},  # load_memory takes no args
        "description": "Loads memory store (read-only check)",
    },
}


def eval_action_execution() -> list[dict]:
    """
    Import and call each action module's entry function with safe arguments.
    Skip dangerous/irreversible tools.
    """
    print(f"\n{'='*72}")
    print(f"  EVALUATION 2 — Action-Layer Execution")
    print(f"{'='*72}\n")

    all_tool_names = [t["name"] for t in TOOL_DECLARATIONS]
    results = []

    for tool_name in all_tool_names:
        if tool_name in SKIP_TOOLS:
            print(f"  ⏭️  {tool_name:<22s} SKIPPED (unsafe/irreversible)")
            results.append({
                "domain":           "Action Execution",
                "command":          f"[direct call] {tool_name}",
                "chosen_tool":      tool_name,
                "expected_tool":    tool_name,
                "tool_correct":     True,
                "execution_status": "SKIPPED",
                "duration_s":       0.0,
                "error":            None,
            })
            continue

        config = ACTION_TEST_CONFIGS.get(tool_name)
        if not config:
            print(f"  ⏭️  {tool_name:<22s} SKIPPED (no test config)")
            results.append({
                "domain":           "Action Execution",
                "command":          f"[direct call] {tool_name}",
                "chosen_tool":      tool_name,
                "expected_tool":    tool_name,
                "tool_correct":     True,
                "execution_status": "SKIPPED",
                "duration_s":       0.0,
                "error":            "No test configuration defined",
            })
            continue

        print(f"  ▶️  {tool_name:<22s} {config['description'][:48]:<48s}", end=" ", flush=True)

        error = None
        returned = None
        t0 = time.perf_counter()

        try:
            import importlib
            mod = importlib.import_module(config["module"])
            func = getattr(mod, config["function"])
            returned = func(**config["args"])
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            traceback.print_exc()

        duration = time.perf_counter() - t0
        status = "PASS" if error is None else "FAIL"
        icon = "✅" if status == "PASS" else "❌"

        returned_str = str(returned)[:120] if returned is not None else ""
        print(f"{icon} {status}  ({duration:.1f}s)  {returned_str[:50]}")

        results.append({
            "domain":           "Action Execution",
            "command":          f"[direct call] {tool_name}",
            "chosen_tool":      tool_name,
            "expected_tool":    tool_name,
            "tool_correct":     True,
            "execution_status": status,
            "duration_s":       round(duration, 3),
            "error":            error,
            "returned":         returned_str,
        })

    return results


# ===================================================================
#  Summary Table
# ===================================================================

def print_summary(all_results: list[dict]):
    print(f"\n{'='*72}")
    print(f"  PER-DOMAIN SUMMARY")
    print(f"{'='*72}\n")

    domain_stats = defaultdict(lambda: {
        "total": 0, "correct": 0, "incorrect": 0,
        "pass": 0, "fail": 0, "skipped": 0
    })

    for r in all_results:
        d = r["domain"]
        s = domain_stats[d]
        s["total"] += 1

        if r.get("tool_correct") is True:
            s["correct"] += 1
        elif r.get("tool_correct") is False:
            s["incorrect"] += 1

        ex = r.get("execution_status", "")
        if ex == "PASS":
            s["pass"] += 1
        elif ex == "FAIL":
            s["fail"] += 1
        elif ex == "SKIPPED":
            s["skipped"] += 1

    # Print header
    print(f"  {'Domain':<32s} {'Total':>5s} {'Correct':>8s} {'Wrong':>6s} {'Pass':>5s} {'Fail':>5s} {'Skip':>5s} {'Accuracy':>9s}")
    print(f"  {'-'*32} {'-'*5} {'-'*8} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*9}")

    overall_total = 0
    overall_correct = 0

    for domain, s in sorted(domain_stats.items()):
        testable = s["correct"] + s["incorrect"]
        accuracy = f"{s['correct']/testable*100:.0f}%" if testable > 0 else "N/A"

        print(f"  {domain:<32s} {s['total']:>5d} {s['correct']:>8d} {s['incorrect']:>6d} "
              f"{s['pass']:>5d} {s['fail']:>5d} {s['skipped']:>5d} {accuracy:>9s}")

        overall_total += testable
        overall_correct += s["correct"]

    overall_acc = f"{overall_correct/overall_total*100:.1f}%" if overall_total > 0 else "N/A"
    print(f"\n  {'OVERALL':<32s} {overall_total:>5d} {overall_correct:>8d} "
          f"{overall_total - overall_correct:>6d} {'':>5s} {'':>5s} {'':>5s} {overall_acc:>9s}")
    print()


# ===================================================================
#  Main
# ===================================================================

def main():
    print("\n" + "█" * 72)
    print("  S.I.V.A  EVALUATION HARNESS")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("█" * 72)

    # Eval 1: Tool Selection
    tool_results = eval_tool_selection()

    # Eval 2: Action Execution
    action_results = eval_action_execution()

    # Merge all results
    all_results = tool_results + action_results

    # Clean up for JSON serialization — remove 'returned' from action results
    # that aren't JSON-safe, and ensure 'chosen_args' dicts are serializable
    for r in all_results:
        # Ensure chosen_args is JSON-safe
        if "chosen_args" in r:
            try:
                json.dumps(r["chosen_args"])
            except (TypeError, ValueError):
                r["chosen_args"] = str(r["chosen_args"])
        # Truncate returned strings
        if "returned" in r and r["returned"]:
            r["returned"] = str(r["returned"])[:500]

    # Write JSON output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n  📁 Results written to: {OUTPUT_PATH}")

    # Print summary
    print_summary(all_results)

    print(f"  Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("█" * 72 + "\n")


if __name__ == "__main__":
    main()
