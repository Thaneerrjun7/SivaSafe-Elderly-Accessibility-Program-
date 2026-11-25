import os
import sys
import json
import asyncio
import datetime
import urllib.request

try:
    from winrt.windows.devices.geolocation import Geolocator, GeolocationAccessStatus
except ImportError:
    Geolocator = None

# Windows lets its location service go to sleep. Measured on this machine, the
# first get_geoposition_async after an idle spell takes 10.2s while the next
# takes 0.4s — that cold scan is what made the weather feel like it needed
# asking twice. Accepting a position up to this old lets Windows hand back the
# fix it already has (0.05s), and the timeout stops a cold scan running long.
_MAX_AGE = datetime.timedelta(minutes=10)
_FIX_TIMEOUT = datetime.timedelta(seconds=8)


async def _get_position(geolocator):
    """Cached fix if Windows has one, else a scan that gives up on time."""
    with_age = getattr(geolocator, "get_geoposition_async_with_age_and_timeout", None)
    if with_age is not None:
        try:
            return await with_age(_MAX_AGE, _FIX_TIMEOUT)
        except Exception:
            # Older winrt builds, or no fix recent enough to reuse.
            pass
    return await geolocator.get_geoposition_async()


async def get_location():
    result = {}
    cache_file = os.path.join(os.path.dirname(__file__), "..", "memory", "location_cache.json")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)

    if not Geolocator:
        result = {"error": "unsupported", "message": "winrt geolocation not installed."}
        _save(cache_file, result)
        print("Error: winrt not installed.")
        return

    try:
        status = await Geolocator.request_access_async()
        if status == GeolocationAccessStatus.ALLOWED:
            geolocator = Geolocator()
            pos = await _get_position(geolocator)
            coord = pos.coordinate
            import time
            result['lat'] = coord.point.position.latitude
            result['lon'] = coord.point.position.longitude
            result['accuracy'] = coord.accuracy
            result['timestamp'] = time.time()

            # Publish the coordinates before looking up what they are called.
            # The weather only needs the numbers; a caller waiting on a first
            # fix should not also wait on Nominatim, which is a different
            # server on a different continent having a different sort of day.
            result['city'] = "your current location"
            _save(cache_file, result)

            # Reverse geocode via Nominatim
            url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={result['lat']}&lon={result['lon']}"
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'SIVA-Assistant'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    address = data.get('address', {})
                    city = address.get('city') or address.get('town') or address.get('state') or 'Unknown'
                    country = address.get('country', '')
                    result['city'] = f"{city}, {country}".strip(', ')
            except:
                pass

            print(f"{result['lat']},{result['lon']},{result['city']}")
        else:
            result = {"error": "denied", "message": "Windows Location Services access denied."}
            print("Access denied.")
    except Exception as e:
        result = {"error": "exception", "message": str(e)}
        print(f"Error: {e}")

    _save(cache_file, result)


def _save(cache_file, result):
    """Write the cache, never losing a good fix to a bad one.

    Windows denies or times out the sensor often enough — a laptop that has
    been asleep, location switched off for a session — and overwriting the
    file with {"error": ...} left the weather with no position at all until
    the next successful read. The error is recorded, the last known
    coordinates are kept alongside it.
    """
    if "lat" not in result:
        previous = {}
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                previous = json.load(f)
        except Exception:
            previous = {}
        if isinstance(previous, dict) and previous.get("lat") is not None:
            for key in ("lat", "lon", "accuracy", "city", "timestamp"):
                if key in previous:
                    result[key] = previous[key]

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(result, f)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(get_location())
