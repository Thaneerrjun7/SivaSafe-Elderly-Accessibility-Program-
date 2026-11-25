import os
import sys
import subprocess
import json
import urllib.request

def location_action(parameters: dict, player=None, session_memory=None) -> str:
    try:
        # Kick off background update
        exe_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "get_native_location.py")
        try:
            subprocess.Popen([sys.executable, exe_path])
        except:
            pass
            
        cache_file = os.path.join(os.path.dirname(__file__), "..", "memory", "location_cache.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    data = json.load(f)
                
                if data.get("error") == "denied":
                    msg = "Sir, Windows Location Services access is denied. Please enable 'Let desktop apps access your location' in Windows Settings."
                elif "city" in data:
                    city = data.get("city", "your current location")
                    msg = f"Sir, according to the native location sensors, you are currently in {city}."
                else:
                    msg = "Sir, I am still acquiring your native coordinates. Please ask again in a few moments."
            except:
                msg = "Sir, I am still acquiring your native coordinates. Please ask again in a few moments."
        else:
            msg = "Sir, I am still acquiring your native coordinates. Please ask again in a few moments."
            
        print(f"[Location] {msg}")
        if player:
            try: player.write_log(f"SIVA: {msg}")
            except: pass
            
        return msg
    except Exception as e:
        msg = f"Sir, I encountered an error checking your location: {e}"
        print(f"[Location] {msg}")
        if player:
            try: player.write_log(f"SIVA: {msg}")
            except: pass
        return msg
