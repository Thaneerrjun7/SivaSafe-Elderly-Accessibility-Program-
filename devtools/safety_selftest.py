r"""
safety_selftest.py — proves actions/content_safety.py without launching SIVA.

No Qt, no auth, no network by default: every case below runs against the
deterministic engine alone, which is the point being demonstrated. If the
offline layer needed the internet to reach a verdict it would be useless on the
day it mattered.

    ..\.venv\Scripts\python.exe devtools\safety_selftest.py
    ..\.venv\Scripts\python.exe devtools\safety_selftest.py --ai   # one live call

Case 8 is the one worth understanding. It is a genuine bank notice containing
the word "immediately", and it must never be called a scam. Any detector can
catch a scam by flagging everything; the false-positive case is what decides
whether an older adult keeps listening to it.
"""

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.content_safety import (            # noqa: E402
    DISCLAIMER, SAFE, CAUTION, DANGER,
    check_text, check_image, extract_urls,
)

_RANK = {SAFE: 0, CAUTION: 1, DANGER: 2}


# ── text cases ─────────────────────────────────────────────────────────
# (name, text, minimum acceptable band, maximum acceptable band)

TEXT_CASES = [
    ("Maybank credential SMS",
     "Maybank: Your account will be suspended within 24 hours. Verify now at "
     "http://maybank2u-verify.top/login and enter your TAC to reactivate.",
     DANGER, DANGER),

    ("LHDN punycode refund",
     "LHDN: You have an unclaimed tax refund of RM1,280. Claim your refund at "
     "https://xn--lhdn-9na.com/claim",
     DANGER, DANGER),

    ("Macau scam call script (no link at all)",
     "This is Inspector Rahman from PDRM. Your bank account is involved in a money "
     "laundering case and a warrant has been issued against you. Do not tell anyone "
     "about this investigation. You must transfer to this account immediately so we "
     "can verify your funds are clean.",
     DANGER, DANGER),

    ("DHL parcel fee via shortener",
     "DHL: Your parcel is held at customs. A delivery fee of RM3.50 is required. "
     "Pay here: bit.ly/dhl-my-pay",
     CAUTION, DANGER),

    ("Touch n Go bare IP address",
     "Touch n Go: your eWallet is frozen. Complete a TNG reload at "
     "http://103.21.244.10/tng-reload to restore access within 24 hours.",
     DANGER, DANGER),

    ("Credential buried before the @ sign",
     "Please log in to verify your account: https://maybank2u.com.my@evil.tk/login",
     DANGER, DANGER),

    ("Ordinary Grab notification",
     "Your Grab driver is arriving in 3 minutes.",
     SAFE, SAFE),

    ("GENUINE bank notice containing 'immediately'  <-- false-positive guard",
     "Maybank: Your monthly statement is ready. Please review it immediately at "
     "https://www.maybank2u.com.my/ if you have any questions.",
     SAFE, CAUTION),
]


# ── image fixtures, built in memory ────────────────────────────────────

def _png(w, h, text_chunks=None) -> bytes:
    from PIL import Image, PngImagePlugin
    img = Image.new("RGB", (w, h), (120, 140, 160))
    meta = PngImagePlugin.PngInfo()
    for k, v in (text_chunks or {}).items():
        meta.add_text(k, v)
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def _jpeg_with_camera(w, h) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (w, h), (200, 180, 150))
    exif = img.getexif()
    exif[271] = "Canon"                       # Make
    exif[272] = "Canon EOS 600D"              # Model
    exif[305] = "Digital Photo Professional"  # Software
    exif[306] = "2026:07:14 09:12:33"         # DateTime
    exif[36867] = "2026:07:14 09:12:33"       # DateTimeOriginal
    exif[33434] = (1, 250)                    # ExposureTime
    exif[33437] = (56, 10)                    # FNumber
    exif[34855] = 200                         # ISOSpeedRatings
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _jpeg_with_c2pa_marker(w, h) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (w, h), (90, 90, 110))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    # Trailing bytes after the end-of-image marker. Crude next to a real C2PA
    # manifest, but it exercises the same byte scan the real one hits.
    return buf.getvalue() + b"\x00c2pa.assertions.trainedAlgorithmicMedia\x00"


def image_cases():
    return [
        ("Stable Diffusion PNG (parameters chunk)",
         _png(1024, 1024, {"parameters": "a photorealistic portrait of an elderly man, "
                                         "Steps: 30, Sampler: DPM++ 2M, CFG scale: 7"}),
         DANGER, DANGER, ["generator settings"]),

        ("Bare 1024x1024 PNG, no metadata at all",
         _png(1024, 1024),
         CAUTION, CAUTION, ["means almost nothing", "weakest signal"]),

        ("Ordinary camera JPEG",
         _jpeg_with_camera(640, 480),
         SAFE, SAFE, ["camera details"]),

        ("File declaring a C2PA AI Content Credential",
         _jpeg_with_c2pa_marker(800, 600),
         DANGER, DANGER, ["Content Credential"]),
    ]


# ── structural rules every verdict must obey ───────────────────────────

def structural_problems(v, expect_findings=True):
    bad = []
    if v.band not in (SAFE, CAUTION, DANGER):
        bad.append(f"band {v.band!r} is not one of the three")
    if not v.headline.strip():
        bad.append("no headline")
    if not v.what_to_do:
        bad.append("no advice")
    if DISCLAIMER not in v.to_dict().get("disclaimer", ""):
        bad.append("disclaimer missing from to_dict()")
    if expect_findings and v.band != SAFE and not v.findings:
        bad.append("flagged but gave no reason — that is the failure this module exists to prevent")
    for f in v.findings:
        if not f.why.strip():
            bad.append(f"finding has no explanation: {f.what[:40]!r}")
        if f.severity not in ("low", "medium", "high"):
            bad.append(f"bad severity {f.severity!r}")
    if not v.spoken_summary().strip():
        bad.append("empty spoken summary")
    return bad


def run(use_ai: bool) -> int:
    passed = failed = 0
    width = 58

    print("\n" + "=" * 78)
    print(f"  SIVA content-safety self-test   (online second opinion: "
          f"{'ON' if use_ai else 'OFF — offline engine only'})")
    print("=" * 78)

    print("\nTEXT\n" + "-" * 78)
    for name, text, lo, hi in TEXT_CASES:
        v = check_text(text, use_ai=use_ai)
        ok = _RANK[lo] <= _RANK[v.band] <= _RANK[hi]
        problems = structural_problems(v)
        ok = ok and not problems
        print(f"  {'PASS' if ok else 'FAIL'}  {name[:width]:<{width}} "
              f"{v.band:<8} score {v.score:>3}  {len(v.findings)} reasons")
        if not ok:
            print(f"        expected {lo}..{hi}, got {v.band}")
            for p in problems:
                print(f"        ! {p}")
            for f in v.findings:
                print(f"        - [{f.severity}] {f.what}")
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)

    print("\nPICTURES\n" + "-" * 78)
    for name, data, lo, hi, must_say in image_cases():
        v = check_image(data, use_ai=use_ai)
        ok = _RANK[lo] <= _RANK[v.band] <= _RANK[hi]
        problems = structural_problems(v, expect_findings=False)
        blob = " ".join(f.what + " " + f.why for f in v.findings)
        missing = [s for s in must_say if s.lower() not in blob.lower()]
        if missing:
            problems.append(f"caveat missing from the explanation: {missing}")
        ok = ok and not problems
        print(f"  {'PASS' if ok else 'FAIL'}  {name[:width]:<{width}} "
              f"{v.band:<8} score {v.score:>3}  {len(v.findings)} reasons")
        if not ok:
            print(f"        expected {lo}..{hi}, got {v.band}")
            for p in problems:
                print(f"        ! {p}")
            for f in v.findings:
                print(f"        - [{f.severity}] {f.what}")
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)

    print("\nURL EXTRACTION\n" + "-" * 78)
    url_checks = [
        ("http://a.com and bit.ly/x plus www.maybank2u.com.my", 3),
        ("no addresses here at all, e.g. nothing", 0),
        ("Call 03-1234 5678 or visit hasil.gov.my today", 1),
    ]
    for text, expected in url_checks:
        got = extract_urls(text)
        ok = len(got) == expected
        print(f"  {'PASS' if ok else 'FAIL'}  {text[:width]:<{width}} "
              f"found {len(got)}, expected {expected}")
        if not ok:
            print(f"        {got}")
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)

    print("\n" + "=" * 78)
    print(f"  {passed} passed, {failed} failed")
    print("=" * 78)

    print("\nSample of what the user actually hears (case 1):\n")
    print("  " + check_text(TEXT_CASES[0][1], use_ai=False).spoken_summary()
          .replace(". ", ".\n  "))
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="store_true",
                    help="also consult Gemini (uses free-tier quota — run sparingly)")
    args = ap.parse_args()
    sys.exit(run(use_ai=args.ai))
