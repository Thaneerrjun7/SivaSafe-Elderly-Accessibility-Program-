"""Exercise the weather report end to end.

Run:  python scripts/test_weather.py

Covers the three things that were broken: a place that is not your own
(cities, countries, regions), the no-city path answering first time, and the
whole thing staying inside its time budget so it can never hang the session.
"""

import os
import sys
import time

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from actions.weather_report import (  # noqa: E402
    COUNTRY_CAPITALS, REGION_CITIES, weather_action, web_weather_url,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# (what to ask for, what must appear in the answer)
CASES = [
    ("Kuala Lumpur", "Kuala Lumpur"),   # plain city
    ("Tokyo",        "Tokyo"),          # plain city, far away
    ("Japan",        "Tokyo"),          # country -> capital, not the centroid
    ("Malaysia",     "Kuala Lumpur"),   # country whose centroid is jungle
    ("India",        "New Delhi"),      # country whose centroid is farmland
    ("the United States", "Washington"),  # phrasing the geocoder returns 0 for
    ("UK",           "London"),         # alias -> country -> capital
    ("California",   "Los Angeles"),    # region the geocoder has no record of
    ("Scotland",     "Edinburgh"),      # region that used to be a US village
    ("Texas",        "Houston"),        # region that used to be in Mexico
    ("Paris",        "Paris"),          # ambiguous, must pick France
    ("Bali",         "Bali"),           # must not pick the village in India
    ("Istanbul",     "Istanbul"),       # non-ASCII country name in the label
    ("Zzzzqqqx",     "could not find"), # nonsense must fail cleanly, not hang
]

BUDGET_S = 16.0     # the ceiling main.py enforces on the tool

failures = []

print("=" * 68)
print("PLACES OTHER THAN YOUR OWN")
print("=" * 68)
for query, expected in CASES:
    start = time.time()
    answer = weather_action({"city": query})
    took = time.time() - start

    ok = expected.casefold() in answer.casefold()
    in_time = took <= BUDGET_S
    if not ok:
        failures.append(f"{query!r}: expected {expected!r} in answer")
    if not in_time:
        failures.append(f"{query!r}: took {took:.1f}s, over the {BUDGET_S}s ceiling")

    print(f"[{'ok ' if ok and in_time else 'FAIL'}] {query:20s} {took:5.2f}s  {answer}")

print()
print("=" * 68)
print("YOUR OWN LOCATION — must answer on the first ask, not the second")
print("=" * 68)
for attempt in (1, 2):
    start = time.time()
    answer = weather_action({})
    took = time.time() - start
    # A real report, not a promise of one. Anything that defers to a later
    # ask is the original bug wearing a different sentence.
    reported = "°C" in answer
    if not reported:
        failures.append(f"no-city ask #{attempt}: no report — said {answer!r}")
    if took > BUDGET_S:
        failures.append(f"no-city ask #{attempt}: took {took:.1f}s")
    print(f"[{'ok ' if reported else 'FAIL'}] ask #{attempt}  {took:5.2f}s  {answer}")

# An empty string means the same as no city at all.
answer = weather_action({"city": "   "})
print(f"[  -  ] blank city    {answer}")
if "could not find" in answer.casefold():
    failures.append("blank city was treated as a place name")

print()
print("=" * 68)
print("TABLES AND THE WEB BUTTON")
print("=" * 68)
print(f"countries: {len(COUNTRY_CAPITALS)}   regions: {len(REGION_CITIES)}")
print(f"web button URL: {web_weather_url()}")
if not web_weather_url().startswith("https://"):
    failures.append("web_weather_url did not return an https URL")

print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All weather checks passed.")
