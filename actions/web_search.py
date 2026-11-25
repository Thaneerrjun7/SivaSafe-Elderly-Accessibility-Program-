#web_search.py
import json
import sys
from pathlib import Path

from config import get_base_dir as _get_base_dir
_base_dir = _get_base_dir


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


from config import get_api_key as _get_api_key

def _get_google_keys() -> tuple[str, str]:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("google_search_api_key", ""), data.get("google_search_cx", "")


def _gemini_search(query: str) -> str:
    from google import genai
    import time

    client   = genai.Client(api_key=_get_api_key())
    
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=query,
                config={"tools": [{"google_search": {}}]},
            )

            text = ""
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text

            text = text.strip()
            if not text:
                raise ValueError("Gemini returned an empty response.")
            return text
        except Exception as e:
            if "503" in str(e) or "Unavailable" in str(e):
                if attempt < 2:
                    print(f"[WebSearch] ⚠️ Gemini 503 error, retrying in 2 seconds (Attempt {attempt + 1}/3)...")
                    time.sleep(2)
                    continue
            raise e


def _google_custom_search(query: str, max_results: int = 5) -> list[dict]:
    import requests
    api_key, cx = _get_google_keys()
    
    if not api_key or not cx or api_key == "YOUR_GOOGLE_SEARCH_API_KEY_HERE":
        raise ValueError("Google Search API keys are missing in config/api_keys.json.")
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": max_results
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    
    results = []
    for item in data.get("items", []):
        results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "url": item.get("link", ""),
        })
    return results


def _format_google(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()

def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data."
    )
    try:
        return _gemini_search(query)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Gemini compare failed: {e} — falling back to Google Custom Search")

    # Google fallback: fetch results per item and merge
    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _google_custom_search(f"{item} {aspect}", max_results=3)
        except Exception as e:
            print(f"[WebSearch] ⚠️ Google search failed for {item}: {e}")
            all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
    return "\n".join(lines)

def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query, sir."

    if items and mode != "compare":
        mode = "compare"

    if player:
        player.write_log(f"[Search] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 Query: {query!r}  Mode: {mode}")

    try:
        if mode == "compare" and items:
            print(f"[WebSearch] 📊 Comparing: {items}")
            result = _compare(items, aspect)
            print("[WebSearch] ✅ Compare done.")
            return result

        print("[WebSearch] 🌐 Trying Gemini...")
        try:
            result = _gemini_search(query)
            print("[WebSearch] ✅ Gemini OK.")
            return result
        except Exception as e:
            print(f"[WebSearch] ⚠️ Gemini failed ({e}) — trying Google Custom Search...")
            results = _google_custom_search(query)
            result  = _format_google(query, results)
            print(f"[WebSearch] ✅ Google Custom Search: {len(results)} result(s).")
            return result

    except Exception as e:
        print(f"[WebSearch] ❌ All backends failed: {e}")
        return f"Search failed, sir: {e}"