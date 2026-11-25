"""
plain_reader.py — Plain Language Document and Screen Reader for SIVA
"""

import io
import json
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from config import get_base_dir as _get_base_dir
_base_dir = _get_base_dir

from config import get_api_key as _get_api_key, get_secondary_api_key as _get_secondary_api_key
def _get_api_keys(): return _get_api_key(), _get_secondary_api_key()

def _capture_screen() -> bytes:
    import mss
    import mss.tools
    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        return mss.tools.to_png(shot.rgb, shot.size)

def _read_with_fallback(text_or_image_part, prompt: str, speak=None):
    from google import genai
    from google.genai import types as gtypes
    from google.genai.errors import APIError
    
    primary_key, secondary_key = _get_api_keys()
    if not primary_key:
        print("[PlainReader] ❌ No Gemini API key found.")
        return "No Gemini API key configured."

    contents = [text_or_image_part, prompt] if text_or_image_part else [prompt]

    def _try_native_audio(api_key: str) -> bool:
        client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=gtypes.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=gtypes.SpeechConfig(
                        voice_config=gtypes.VoiceConfig(
                            prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(voice_name="Aoede")
                        )
                    )
                )
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    print("[PlainReader] 🔊 Playing Native Audio...")
                    sys.path.append(str(_base_dir()))
                    from core.tts import _play_audio_bytes
                    _play_audio_bytes(part.inline_data.data)
                    return True
            return False
        except APIError as e:
            if e.code == 429: raise e
            print(f"[PlainReader] ⚠️ Native Audio failed ({e.code}).")
            return False
        except Exception as e:
            print(f"[PlainReader] ⚠️ Native Audio failed ({e}).")
            return False

    def _try_edgetts(api_key: str) -> bool:
        client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents
            )
            simplified_text = response.text.strip()
            print("[PlainReader] 🗣️ Reading with EdgeTTS (Aria)...")
            sys.path.append(str(_base_dir()))
            from core.tts import EdgeTTSEngine
            engine = EdgeTTSEngine(voice="en-US-AriaNeural")
            engine.speak(simplified_text)
            return True
        except APIError as e:
            if e.code == 429: raise e
            print(f"[PlainReader] ❌ EdgeTTS fallback failed: {e}")
            return False
        except Exception as e:
            print(f"[PlainReader] ❌ EdgeTTS fallback failed: {e}")
            return False

    # --- Step 1: Try Native Audio on Primary Key ---
    print("[PlainReader] 🗣️ Requesting Gemini Native Audio (Aoede, Primary Key)...")
    try:
        if _try_native_audio(primary_key): return "Read aloud successfully using Gemini Native Voice (Primary)."
    except APIError as e:
        if e.code == 429: print("[PlainReader] 🔄 Quota Exceeded on Primary Key!")

    # --- Step 2: Try Native Audio on Secondary Key ---
    if secondary_key:
        print("[PlainReader] 🗣️ Requesting Gemini Native Audio (Aoede, Secondary Key)...")
        try:
            if _try_native_audio(secondary_key): return "Read aloud successfully using Gemini Native Voice (Secondary)."
        except APIError as e:
            if e.code == 429: print("[PlainReader] 🔄 Quota Exceeded on Secondary Key!")

    # --- Step 3: Try EdgeTTS Fallback on Primary Key ---
    print("[PlainReader] 🧠 Generating plain language text for EdgeTTS (Primary Key)...")
    try:
        if _try_edgetts(primary_key): return "Read aloud successfully using fallback TTS (Primary)."
    except APIError as e:
        if e.code == 429: print("[PlainReader] 🔄 Quota Exceeded on Primary Key (Text Gen)!")

    # --- Step 4: Try EdgeTTS Fallback on Secondary Key ---
    if secondary_key:
        print("[PlainReader] 🧠 Generating plain language text for EdgeTTS (Secondary Key)...")
        try:
            if _try_edgetts(secondary_key): return "Read aloud successfully using fallback TTS (Secondary)."
        except Exception:
            pass

        if speak:
            speak("I am sorry sir, I failed to read the text. API quotas might be exhausted or an error occurred.")
    return "Failed to read out loud. All API quotas have been exhausted or failed."

def read_plain_language(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    """
    Action hook for reading the screen or a file in plain language.
    """
    file_path = parameters.get("file_path", "").strip()
    raw_text = parameters.get("raw_text", "").strip()
    
    prompt = (
        "You are acting as a Plain Language Reader for an elderly or non-technical user. "
        "Review this email or document. First, offer a very brief 1-2 sentence summary of what this is. "
        "Then, provide a simplified version of the text. "
        "Do NOT remove the technical jargon completely or strip out important news/information, "
        "but DO explain it in a way that is easy to understand and flows like a simplified news report. "
        "Output ONLY the spoken text you want to read aloud."
    )
    
    part = None
    if raw_text:
        print("[PlainReader] 📄 Processing direct raw text...")
        part = raw_text
    elif file_path and os.path.exists(file_path):
        print(f"[PlainReader] 📄 Processing file: {file_path}")
        try:
            # Re-use file processor logic or just read text if simple
            ext = file_path.lower().split('.')[-1]
            if ext in ['txt', 'md', 'csv', 'json']:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                part = text
            else:
                # PDF, Word, PowerPoint and the rest. extract_text returns the
                # document's words; file_processor's own "extract_text" action
                # returns a sentence describing a file it just saved, which is
                # not what we want to read to somebody.
                sys.path.append(str(_base_dir()))
                from actions.file_processor import extract_text
                part = extract_text(file_path)

            if not str(part).strip():
                return "That file has no text in it that I can read."
        except Exception as e:
            return f"Failed to read file: {e}"
    else:
        print("[PlainReader] 🖥️ Capturing screen for email/document...")
        try:
            png_bytes = _capture_screen()
            from google.genai import types as gtypes
            part = gtypes.Part.from_bytes(data=png_bytes, mime_type="image/png")
        except Exception as e:
            return f"Failed to capture screen: {e}"

    threading.Thread(target=_read_with_fallback, args=(part, prompt, speak), daemon=True).start()
    
    return "I am analyzing the document now and will read it out loud shortly."
