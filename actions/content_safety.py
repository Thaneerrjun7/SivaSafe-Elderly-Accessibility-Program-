"""
content_safety.py — Scam, phishing and AI-content screening for SIVA.

This module answers one question for an older adult: *is the thing in front of
me trying to cheat me?* — and, just as importantly, *why do you say so?*

Three things shape the design.

**It explains, it does not pronounce.** Chapter 2 of the report identified the
gap: existing scam detectors sit away from the point of interaction and hand
down a verdict with no reasoning. A bare "SUSPICIOUS" moves the decision back
onto the person least equipped to make it. So the unit of output here is not a
score, it is a `Finding` — what was seen, why it matters, and what it does *not*
prove. The score exists only to sort and to band.

**The offline engine is the product; the model is a second opinion.** Every
verdict is fully formed — findings, advice, band, disclaimer — before Gemini is
ever consulted, and stays fully formed if there is no key, no network or no
quota left. The model may raise a band by one step, never lower it, never by
two. Failure direction is always toward caution.

**Nothing here is authoritative, and the output says so.** `DISCLAIMER` is a
field on every `Verdict` rather than something the interface is trusted to
remember. A metadata tag is a *claim made by a file*, not a measurement of its
pixels; the absence of metadata means almost nothing once WhatsApp has been near
it. Those caveats are written into the findings themselves, because they are the
honest part.

No Qt imports, no global state, no I/O beyond reading the file it is given —
which is what lets `devtools/safety_selftest.py` prove it in two seconds without
launching the application.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, unquote

# Optional at import time. Running standalone (the self-test) must not need the
# app's config package, and a machine with no Pillow must still screen text.
try:
    from config import get_vision_api_keys as _get_vision_keys
except Exception:                                    # pragma: no cover
    def _get_vision_keys() -> list[str]:
        return []

try:
    import PIL.Image
    import PIL.ExifTags
    _PIL = True
except Exception:                                    # pragma: no cover
    _PIL = False


DISCLAIMER = (
    "This is a guide, not a guarantee. SIVA can be wrong in both directions. "
    "When money, passwords or one-time codes are involved, check with someone "
    "you trust before you act."
)

SAFE, CAUTION, DANGER = "SAFE", "CAUTION", "DANGER"
_ORDER = {SAFE: 0, CAUTION: 1, DANGER: 2}
_BY_RANK = {0: SAFE, 1: CAUTION, 2: DANGER}


# ═══════════════════════════════════════════════════════════════════════
# RESULT TYPES
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Finding:
    """One observation, in the user's language.

    `why` carries a double duty: it says why the observation matters *and* what
    it does not prove. The second half is the part that keeps the feature
    honest, and it is the part an examiner will press on.
    """
    what: str
    why: str
    severity: str = "medium"          # low | medium | high
    source: str = "wording"           # link | wording | picture | email-headers | ai

    def to_dict(self) -> dict:
        return {"what": self.what, "why": self.why,
                "severity": self.severity, "source": self.source}


@dataclass
class Verdict:
    band: str = SAFE                  # SAFE | CAUTION | DANGER
    kind: str = "text"                # text | link | email | document | image | screen
    score: int = 0                    # 0..100, the deterministic total
    confidence: str = "low"           # low | medium | high
    headline: str = ""
    # Short label for the badge. Lives here rather than in the interface
    # because "this looks like a scam" and "this was probably made by AI" are
    # two different claims that happen to share a colour, and only the engine
    # knows which one it just made.
    chip: str = ""
    findings: list[Finding] = field(default_factory=list)
    what_to_do: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    degraded: bool = False            # the online second opinion could not run
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "band": self.band, "kind": self.kind, "score": self.score,
            "confidence": self.confidence, "headline": self.headline,
            "chip": self.chip,
            "findings": [f.to_dict() for f in self.findings],
            "what_to_do": list(self.what_to_do),
            "checked": list(self.checked),
            "degraded": self.degraded,
            "disclaimer": self.disclaimer,
        }

    def spoken_summary(self, max_findings: int = 3) -> str:
        """A version meant for the ear, not the eye.

        No bullets, no web addresses spelled out letter by letter, and never
        more than three reasons — past that a listener has stopped counting.
        Ends on the disclaimer, because that is the sentence that must survive
        being half-heard.
        """
        parts = [self.headline.rstrip(".") + "."]
        shown = [f for f in self.findings if f.severity in ("high", "medium")][:max_findings]
        if not shown:
            shown = self.findings[:max_findings]
        if shown:
            parts.append("Here is why. " + " ".join(f.what.rstrip(".") + "." for f in shown))
        if self.what_to_do:
            parts.append("What I would do: " + "; ".join(
                a.rstrip(".").lower() for a in self.what_to_do[:3]) + ".")
        if self.degraded:
            parts.append("My online check could not run, so this is from the offline checks only.")
        parts.append(DISCLAIMER)
        return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# DOMAIN PARSING
# ═══════════════════════════════════════════════════════════════════════

# An approximation of the Public Suffix List, not the list itself. `tldextract`
# is not a dependency of this project and demo eve is the wrong time to add one.
# These are the multi-part suffixes that actually matter for a Malaysian user;
# anything else falls back to "last two labels", which is right far more often
# than it is wrong. Stated plainly here so the limitation is not discovered
# later as a surprise.
_MULTI_SUFFIXES = {
    "com.my", "net.my", "org.my", "gov.my", "edu.my", "mil.my", "name.my",
    "com.sg", "com.au", "com.hk", "com.tw", "com.cn", "com.br", "com.ph",
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.id", "or.id", "my.id",
    "co.jp", "co.in", "co.th", "co.kr", "co.nz", "co.za",
}

# Bare hostnames (no scheme) are only believed when they end in something from
# this set. Without it, "e.g", "vs.the" and every filename with a dot become
# domains, and the false-positive rate makes the whole feature untrustworthy.
_KNOWN_TLDS = {
    "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "name",
    "my", "sg", "id", "th", "ph", "vn", "hk", "tw", "cn", "jp", "kr", "in",
    "au", "nz", "uk", "ie", "de", "fr", "es", "it", "nl", "be", "ch", "at",
    "se", "no", "dk", "fi", "pl", "pt", "gr", "cz", "ru", "ua", "tr", "br",
    "mx", "ar", "cl", "co", "ca", "us", "za", "ae", "sa", "eg", "ng", "ke",
    "io", "ai", "app", "dev", "me", "tv", "cc", "online", "site", "store",
    "shop", "blog", "cloud", "email", "live", "life", "world", "today",
    # short country codes the URL shorteners live on — without these, "bit.ly"
    # and "cutt.ly" are not recognised as addresses at all
    "ly", "gd", "gg", "do", "st", "sh", "to", "ws", "im", "am", "fm", "is",
    # the risky set, so they are never missed
    "zip", "mov", "top", "xyz", "tk", "ml", "ga", "cf", "gq", "click", "link",
    "work", "rest", "buzz", "cam", "quest", "sbs", "cfd", "icu", "fit", "boats",
}

_RISKY_TLDS = {
    "zip", "mov", "top", "xyz", "tk", "ml", "ga", "cf", "gq", "click", "link",
    "work", "rest", "buzz", "cam", "quest", "sbs", "cfd", "icu", "fit", "boats",
    "lol", "monster", "bar", "kim", "country", "download", "review", "stream",
}

_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "rb.gy", "shorturl.at",
    "ow.ly", "s.id", "bit.do", "tiny.cc", "buff.ly", "rebrand.ly", "t.ly",
    "shorte.st", "adf.ly", "gg.gg", "urlz.fr", "v.gd", "clck.ru",
}

# token -> the real domain. Holding the canonical domain (not just the name) is
# what lets a finding say "the real one is maybank2u.com.my" — and that single
# sentence is the most useful thing this module produces.
_BRANDS: dict[str, str] = {
    # Malaysian banking and payments
    "maybank": "maybank2u.com.my", "maybank2u": "maybank2u.com.my",
    "cimb": "cimbclicks.com.my", "cimbclicks": "cimbclicks.com.my",
    "publicbank": "pbebank.com", "pbebank": "pbebank.com",
    "rhb": "rhbgroup.com", "hongleong": "hlb.com.my", "hlb": "hlb.com.my",
    "ambank": "ambank.com.my", "bankislam": "bankislam.com",
    "bsn": "bsn.com.my", "bankrakyat": "bankrakyat.com.my",
    "ocbc": "ocbc.com.my", "uob": "uob.com.my", "hsbc": "hsbc.com.my",
    "touchngo": "touchngo.com.my", "tngdigital": "touchngo.com.my",
    "tng": "touchngo.com.my", "boost": "myboost.com.my",
    "duitnow": "duitnow.my", "grabpay": "grab.com", "grab": "grab.com",
    # Malaysian government and utilities
    "lhdn": "hasil.gov.my", "hasil": "hasil.gov.my",
    "jpj": "jpj.gov.my", "myeg": "myeg.com.my", "jpn": "jpn.gov.my",
    "kwsp": "kwsp.gov.my", "epf": "kwsp.gov.my",
    "perkeso": "perkeso.gov.my", "socso": "perkeso.gov.my",
    "pdrm": "rmp.gov.my", "imigresen": "imi.gov.my",
    "tnb": "tnb.com.my", "syabas": "airselangor.com",
    "unifi": "unifi.com.my", "maxis": "maxis.com.my",
    "celcom": "celcom.com.my", "digi": "digi.com.my", "astro": "astro.com.my",
    "pos": "pos.com.my", "poslaju": "pos.com.my",
    # Global
    "paypal": "paypal.com", "google": "google.com", "gmail": "google.com",
    "apple": "apple.com", "icloud": "apple.com",
    "microsoft": "microsoft.com", "outlook": "microsoft.com",
    "netflix": "netflix.com", "amazon": "amazon.com",
    "facebook": "facebook.com", "instagram": "instagram.com",
    "whatsapp": "whatsapp.com", "telegram": "telegram.org",
    "shopee": "shopee.com.my", "lazada": "lazada.com.my",
    "dhl": "dhl.com", "fedex": "fedex.com", "ups": "ups.com",
    "binance": "binance.com", "luno": "luno.com", "coinbase": "coinbase.com",
}

_URL_RE = re.compile(r'https?://[^\s<>"\'\)\]\},]+', re.IGNORECASE)
_BARE_HOST_RE = re.compile(
    r'(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})'
    r'(?:/[^\s<>"\'\)\]\},]*)?',
    re.IGNORECASE)


def _levenshtein(a: str, b: str, cap: int = 3) -> int:
    """Edit distance, two rolling rows, no dependency.

    Short-circuits on a length gap wider than `cap` because the only question
    ever asked of it is "is this within one or two edits of a brand name?".
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _registrable(host: str) -> tuple[str, str]:
    """Split a host into (registrable domain, public suffix)."""
    host = host.strip(".").lower()
    labels = host.split(".")
    if len(labels) < 2:
        return host, ""
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:]), last_two
    return last_two, labels[-1]


def extract_urls(text: str) -> list[str]:
    """Every web address in the text, schemed ones first, deduped in order."""
    found: list[str] = []
    seen: set[str] = set()

    for m in _URL_RE.finditer(text or ""):
        u = m.group(0).rstrip('.,;:!?)"\'')
        if u.lower() not in seen:
            seen.add(u.lower())
            found.append(u)

    covered = " ".join(found).lower()
    for m in _BARE_HOST_RE.finditer(text or ""):
        host = m.group(1).rstrip(".")
        if host.split(".")[-1].lower() not in _KNOWN_TLDS:
            continue
        if host.lower() in covered:
            continue
        u = "http://" + m.group(0).rstrip('.,;:!?)"\'')
        if u.lower() not in seen:
            seen.add(u.lower())
            found.append(u)

    return found


# ═══════════════════════════════════════════════════════════════════════
# LAYER A — LINK STRUCTURE
# ═══════════════════════════════════════════════════════════════════════

def _analyse_url(url: str, login_wording: bool) -> tuple[list[Finding], int]:
    """Structural markers of phishing in a single web address."""
    out: list[Finding] = []
    pts = 0
    try:
        parts = urlsplit(url)
    except Exception:
        return out, 0

    host = (parts.hostname or "").lower()
    if not host:
        return out, 0

    netloc = parts.netloc or ""
    reg, _suffix = _registrable(host)
    reg_label = reg.split(".")[0]
    tld = host.split(".")[-1]
    whole = unquote(url).lower()

    # Everything before an "@" is a username, not the destination. It is the
    # oldest trick in the book and still the most convincing to read.
    if "@" in netloc:
        out.append(Finding(
            f"The address is written to look like it goes to “{netloc.split('@')[0][:40]}”, "
            f"but it actually goes to {host}.",
            "Anything before the @ sign in a web address is ignored by the browser. "
            "The real destination is the part straight after it. This trick exists "
            "for no honest reason.",
            "high", "link"))
        pts += 35

    if "xn--" in host:
        out.append(Finding(
            f"The address {host} is disguised using look-alike letters.",
            "“xn--” means the address contains characters that are not ordinary English "
            "letters — for example a Cyrillic “а” that looks identical to an English “a”. "
            "Real companies do not need to do this.",
            "high", "link"))
        pts += 35
    elif any(ord(c) > 127 for c in host):
        out.append(Finding(
            f"The address {host} contains letters that are not ordinary English letters.",
            "Some alphabets have characters that look identical to English ones, and they "
            "are used to imitate a real address. It does not prove the site is fake, but "
            "there is rarely a good reason for it.",
            "high", "link"))
        pts += 30

    try:
        ipaddress.ip_address(host)
        out.append(Finding(
            f"The link goes to a bare number, {host}, instead of a company name.",
            "Legitimate banks, shops and government departments use a name, not a raw "
            "numeric address. This is normal inside a company's own network, but not in "
            "a message sent to you.",
            "high", "link"))
        pts += 30
    except ValueError:
        pass

    # Brand impersonation. Scan the whole address, not just the host — the
    # brand often sits in the path or in the username-before-@ precisely
    # because that is the part a reader trusts and the browser ignores.
    for token, canonical in _BRANDS.items():
        if len(token) < 3:
            continue
        hit = re.search(rf'(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])', whole)
        near = (len(reg_label) >= 5 and 1 <= _levenshtein(reg_label, token) <= 2)
        if not hit and not near:
            continue
        if reg == canonical or host == canonical or host.endswith("." + canonical):
            continue                                  # it really is them
        if near and not hit:
            out.append(Finding(
                f"The address {host} is spelled almost — but not exactly — like {token}. "
                f"The real one is {canonical}.",
                "Changing or dropping one letter is the standard way to imitate a "
                "well-known name. Read the address slowly, letter by letter.",
                "high", "link"))
        else:
            out.append(Finding(
                f"The link mentions {token}, but it goes to {host}, not to {canonical}.",
                f"A message that claims to be from {token} should lead to "
                f"{canonical}. Putting a trusted name somewhere else in the address is "
                "how a fake page is made to look real.",
                "high", "link"))
        pts += 40
        break

    if reg in _SHORTENERS or host in _SHORTENERS:
        out.append(Finding(
            f"The link is shortened ({host}), so you cannot see where it really goes.",
            "Shortened links are used honestly all the time, but they also hide the "
            "destination completely — which is exactly why scams favour them. On its own "
            "this proves nothing.",
            "medium", "link"))
        pts += 12

    if tld in _RISKY_TLDS:
        out.append(Finding(
            f"The address ends in “.{tld}”, an ending heavily used by scam sites.",
            f"“.{tld}” addresses are cheap or free to register and are abandoned quickly. "
            "Plenty of harmless sites use them too, so this is a reason to look closer, "
            "not a conclusion.",
            "medium", "link"))
        pts += 15

    depth = len(host.split(".")) - len(reg.split("."))
    if depth > 3:
        out.append(Finding(
            f"The address has an unusual number of parts before the real name: {host}.",
            "Long chains like this are used to push the real destination off the edge of "
            "a phone screen, so only the trustworthy-looking beginning is visible.",
            "medium", "link"))
        pts += 12

    if reg_label.count("-") >= 2:
        out.append(Finding(
            f"The name “{reg_label}” is stitched together with hyphens.",
            "Made-up addresses often join a brand and a word like “verify” or “secure” "
            "with hyphens. Some real businesses do this too.",
            "low", "link"))
        pts += 8

    if parts.scheme == "http" and login_wording:
        out.append(Finding(
            "The link is not secure (it starts with http, not https) yet asks you to sign in.",
            "Without the “s” the information you type travels unprotected. No bank login "
            "page works this way.",
            "medium", "link"))
        pts += 10

    return out, pts


# ═══════════════════════════════════════════════════════════════════════
# LAYER A — WORDING
# ═══════════════════════════════════════════════════════════════════════

# Grouped into named categories on purpose. The grouping is what makes the
# score defensible: it is not "how many scary words", it is "how many different
# pressure tactics are being applied at once".
_MARKERS: dict[str, tuple[int, str, list[str], str, str]] = {
    "otp": (35, "high", [
        r"\btac\b", r"\botp\b", r"one[- ]time (?:password|code|pin)",
        r"verification code", r"\b6[- ]digit code\b", r"read me the code",
        r"share the code", r"kod pengesahan",
    ], "You are being asked for a one-time code.",
        "A one-time code is the last thing standing between a stranger and your money. "
        "No bank, no police officer and no delivery company will ever ask for it. Anyone "
        "who does is trying to take something."),

    "payment_rail": (25, "high", [
        r"gift card", r"itunes card", r"google play card", r"steam card",
        r"\bbitcoin\b", r"\busdt\b", r"crypto ?wallet", r"wire transfer",
        r"western union", r"duitnow", r"touch ?'?n ?go reload", r"tng reload",
        r"cash deposit machine", r"\bcdm\b", r"transfer to (?:this|the following) account",
        r"bank[- ]in to", r"pindahkan wang",
    ], "It asks you to send money in a way that cannot be reversed.",
        "Gift cards, crypto and cash deposits are favoured by scams because once the money "
        "moves it is gone for good and cannot be traced back. A real bill is never paid "
        "this way."),

    "arrest_threat": (25, "high", [
        r"\bwarrant\b", r"\barrest\b", r"money laundering", r"remand",
        r"court order", r"\bsaman\b", r"you will be charged", r"legal action against you",
        r"your account is involved", r"under investigation",
    ], "It threatens you with arrest or legal trouble.",
        "This is the pattern of the “Macau scam”: someone posing as police or a bank "
        "frightens the person into moving money to a “safe account”. Real authorities do "
        "not phone people about warrants, and never ask for transfers."),

    "secrecy": (20, "high", [
        r"do not tell", r"don'?t tell", r"keep this confidential",
        r"between us", r"do not discuss", r"jangan beritahu",
    ], "You are being told to keep it to yourself.",
        "Isolation is deliberate. Nothing legitimate requires you to hide it from your "
        "own family — the secrecy exists to stop anyone talking you out of it."),

    "credential": (22, "high", [
        r"verify your account", r"confirm your identity", r"reactivate your account",
        r"update your password", r"log ?in to (?:confirm|verify|avoid)",
        r"account will be (?:closed|suspended|terminated|blocked)",
        r"click (?:the |this )?link below to (?:log ?in|sign ?in|verify)",
        r"sahkan akaun",
    ], "It wants you to log in or confirm your details through its link.",
        "This is how sign-in details are harvested: the page is a copy of the real one. "
        "If you think it might be genuine, go to the site the way you normally do instead "
        "of following the link."),

    "authority": (18, "medium", [
        r"\bpdrm\b", r"\bpolis\b", r"\bpolice\b", r"bank negara", r"\blhdn\b",
        r"inland revenue", r"\bkastam\b", r"\bcustoms\b", r"jabatan imigresen",
        r"we are calling from", r"this is officer", r"\binspector\b",
    ], "It claims to come from the police, a bank or a government department.",
        "Impersonating an authority is what makes the rest of the message work. The name "
        "on a message proves nothing — anyone can type it."),

    "too_good": (18, "medium", [
        r"you have won", r"congratulations[, ]", r"lucky draw", r"\bprize\b",
        r"free gift", r"claim your", r"tax refund", r"unclaimed",
        r"inheritance", r"guaranteed (?:profit|return)", r"100% profit",
        r"risk[- ]free", r"anda telah memenangi",
    ], "It offers you money, a prize or a refund you did not ask for.",
        "Unexpected winnings and surprise refunds are the oldest opening there is. The "
        "prize is the bait; the fee or the details you are asked for afterwards is the point."),

    "parcel": (15, "medium", [
        r"parcel (?:is )?(?:held|on hold|pending)", r"customs (?:duty|fee|charge)",
        r"shipment (?:held|pending)", r"delivery fee", r"redelivery",
        r"unable to deliver", r"pos ?laju", r"bungkusan",
    ], "It says a parcel is stuck and needs a payment.",
        "Delivery companies collect charges through their own app or website, not through "
        "a link in a message. This one is very common because almost everyone is waiting "
        "for something."),

    "urgency": (12, "medium", [
        r"\burgent\b", r"\bimmediately\b", r"within 24 hours", r"within 48 hours",
        r"act now", r"final notice", r"last warning", r"expires? (?:today|soon|in)",
        r"\bsegera\b", r"failure to (?:comply|respond)",
    ], "It pushes you to act quickly.",
        "Time pressure is there to stop you checking. On its own it means little — real "
        "notices have deadlines too — but it is the ingredient every scam needs."),
}


def _analyse_wording(text: str) -> tuple[list[Finding], int, set[str]]:
    low = (text or "").lower()
    out: list[Finding] = []
    pts = 0
    hit_categories: set[str] = set()

    for name, (points, sev, patterns, what, why) in _MARKERS.items():
        for pat in patterns:
            if re.search(pat, low):
                out.append(Finding(what, why, sev, "wording"))
                pts += points
                hit_categories.add(name)
                break

    body = (text or "").strip()
    if len(body) > 80:
        letters = [c for c in body if c.isalpha()]
        if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.30:
            out.append(Finding(
                "Much of the message is in capital letters.",
                "Shouting is used to create alarm. It is a style, not proof of anything.",
                "low", "wording"))
            pts += 5
            hit_categories.add("style")
    if re.search(r"[!]{3,}", body):
        out.append(Finding(
            "The message uses rows of exclamation marks.",
            "Organisations writing to customers do not do this. It is a weak signal on its own.",
            "low", "wording"))
        pts += 5
        hit_categories.add("style")

    return out, pts, hit_categories


# ═══════════════════════════════════════════════════════════════════════
# LAYER A — EMAIL HEADERS (.eml)
# ═══════════════════════════════════════════════════════════════════════

def _domain_of(addr: str) -> str:
    m = re.search(r'@([A-Za-z0-9.\-]+)', addr or "")
    return _registrable(m.group(1))[0] if m else ""


def _analyse_eml(path: str) -> tuple[list[Finding], int, str]:
    """Header-level markers, plus the message body for the text engine."""
    import email
    import email.policy

    out: list[Finding] = []
    pts = 0
    try:
        with open(path, "rb") as fh:
            msg = email.message_from_binary_file(fh, policy=email.policy.default)
    except Exception as e:
        return [Finding("I could not open that email file.",
                        f"The file may be damaged or in a format I do not read ({e}).",
                        "low", "email-headers")], 0, ""

    frm = str(msg.get("From", ""))
    from_dom = _domain_of(frm)
    display = re.sub(r'<[^>]*>', '', frm).strip(' "\'')

    for token, canonical in _BRANDS.items():
        if len(token) < 3:
            continue
        if re.search(rf'(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])', display.lower()):
            if from_dom and from_dom != canonical and not from_dom.endswith("." + canonical):
                out.append(Finding(
                    f"The sender's name says “{display}”, but the email actually comes "
                    f"from {from_dom}, not {canonical}.",
                    "The name shown on an email is typed by whoever sent it and is never "
                    "checked by anyone. Only the address after the @ matters.",
                    "high", "email-headers"))
                pts += 35
            break

    for header, points in (("Return-Path", 25), ("Reply-To", 25)):
        val = str(msg.get(header, ""))
        dom = _domain_of(val)
        if dom and from_dom and dom != from_dom:
            out.append(Finding(
                f"A reply to this email would go to {dom}, not to {from_dom}.",
                "Genuine mail almost always replies to the address it came from. A "
                "mismatch means your answer lands somewhere you did not expect. Mailing "
                "lists do this legitimately, so it is not proof by itself.",
                "high", "email-headers"))
            pts += points

    auth = str(msg.get("Authentication-Results", "")).lower()
    if auth:
        if any(f"{k}=fail" in auth for k in ("dkim", "spf", "dmarc")):
            out.append(Finding(
                "The sender's address failed its authenticity checks.",
                "The receiving mail server tried to confirm the message really came from "
                "the domain it claims, and the check failed. Misconfigured senders fail "
                "these too, but combined with anything else it matters.",
                "high", "email-headers"))
            pts += 30
        elif any(f"{k}=pass" in auth for k in ("dkim", "spf", "dmarc")):
            out.append(Finding(
                "The sender's address passed its authenticity checks.",
                "The message really was sent by the domain it claims. That does not make "
                "the contents true — a scammer's own domain passes its own checks.",
                "low", "email-headers"))
            pts -= 10

    body = ""
    html = ""
    try:
        tp = msg.get_body(preferencelist=("plain",))
        if tp:
            body = tp.get_content()
        th = msg.get_body(preferencelist=("html",))
        if th:
            html = th.get_content()
    except Exception:
        pass

    if html:
        # (href, anchor text) — the destination and the words shown for it are
        # two independent things, and when they disagree the words are the
        # disguise. Only worth comparing when the text itself looks like a URL;
        # "Click here" tells you nothing.
        anchors = re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                             html, re.IGNORECASE | re.DOTALL)[:40]
        for href, anchor_text in anchors:
            shown = re.sub(r'<[^>]+>', '', anchor_text).strip()
            if not re.match(r'^\s*https?://', shown, re.IGNORECASE):
                continue
            try:
                shown_host = (urlsplit(shown).hostname or "").lower()
                real_host = (urlsplit(href).hostname or "").lower()
            except Exception:
                continue
            if shown_host and real_host and _registrable(shown_host)[0] != _registrable(real_host)[0]:
                out.append(Finding(
                    f"A link is written as {shown_host} but actually goes to {real_host}.",
                    "The words in a link and its destination are two separate things. When "
                    "they disagree, the words are the disguise.",
                    "high", "email-headers"))
                pts += 35
                break

        if not body:
            body = re.sub(r'<[^>]+>', ' ', html)

    subject = str(msg.get("Subject", ""))
    return out, pts, f"{subject}\n{body}\n{html}"


# ═══════════════════════════════════════════════════════════════════════
# LAYER B — IMAGE FORENSICS
# ═══════════════════════════════════════════════════════════════════════

_GENERATOR_RE = re.compile(
    r"midjourney|dall.?e|openai|stable.?diffusion|automatic1111|comfyui|invokeai|"
    r"firefly|adobe firefly|grok|xai|imagen|flux|leonardo|nightcafe|novelai|"
    r"craiyon|bing image creator|designer|playground ?ai|ideogram|recraft",
    re.IGNORECASE)

_GEN_CANVASES = {
    (512, 512), (768, 768), (1024, 1024), (1280, 1280), (2048, 2048),
    (1024, 1792), (1792, 1024), (1024, 1536), (1536, 1024),
    (1152, 896), (896, 1152), (1344, 768), (768, 1344), (1216, 832), (832, 1216),
}


def _image_forensics(data: bytes) -> tuple[list[Finding], int, bool]:
    """What the file says about its own origin.

    Every signal here is a *claim recorded in the file*, not a measurement of
    the pixels. That distinction is written into each `why`, because it is the
    honest limit of metadata-based detection and the first thing worth being
    asked about.
    """
    out: list[Finding] = []
    pts = 0

    raw = data if len(data) < 8 * 1024 * 1024 else data[:512 * 1024] + data[-256 * 1024:]
    low = raw.lower()

    if b"trainedalgorithmicmedia" in low:
        out.append(Finding(
            "The file carries a Content Credential saying it was made by an AI generator.",
            "C2PA Content Credentials are a signed record attached by the software that "
            "made the image. SIVA reads the record but does not verify the signature, so "
            "treat it as a strong claim rather than proof.",
            "high", "picture"))
        pts += 50
    elif any(m in low for m in (b"c2pa", b"contentcredentials", b"urn:c2pa", b"jumbf")):
        out.append(Finding(
            "The file carries a provenance record about how it was made.",
            "Something has attached a history to this image. SIVA cannot verify the "
            "signature on it, and the record does not say the image is artificial.",
            "medium", "picture"))
        pts += 10

    if not _PIL:
        out.append(Finding(
            "I could only check the file's raw contents, not its picture details.",
            "The imaging library is unavailable on this computer, so the deeper checks "
            "did not run.",
            "low", "picture"))
        return out, pts, True

    import io
    try:
        img = PIL.Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        out.append(Finding(
            "I could not open this picture.",
            f"The file may be damaged, or it may not be a picture at all ({e}).",
            "low", "picture"))
        return out, pts, True

    info = dict(img.info or {})
    for key in ("parameters", "prompt", "workflow", "sd-metadata", "Dream", "Comment"):
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            if key in ("Comment",) and not _GENERATOR_RE.search(val):
                continue
            out.append(Finding(
                f"The picture file contains generator settings under “{key}”.",
                "Stable Diffusion and ComfyUI write the prompt and settings straight into "
                "the file when they save it. Here is the start of it: "
                f"“{val.strip()[:120]}”. A tag can be copied or faked, so it is a claim "
                "the file makes about itself.",
                "high", "picture"))
            pts += 45
            break

    exif = {}
    try:
        raw_exif = img.getexif()
        if raw_exif:
            exif = {PIL.ExifTags.TAGS.get(k, k): v for k, v in raw_exif.items()}
    except Exception:
        exif = {}

    software = str(exif.get("Software", "") or "")
    if software and _GENERATOR_RE.search(software):
        out.append(Finding(
            f"The file says it was created by “{software.strip()[:60]}”.",
            "The image records the software that saved it, and that name belongs to an "
            "AI image generator. This is a claim written in the file, not a measurement "
            "of the picture — it can be removed or forged.",
            "high", "picture"))
        pts += 45

    make, model = str(exif.get("Make", "") or ""), str(exif.get("Model", "") or "")
    capture_fields = [f for f in ("DateTimeOriginal", "ExposureTime", "FNumber",
                                  "ISOSpeedRatings", "LensModel", "GPSInfo")
                      if exif.get(f) not in (None, "")]

    if make and model:
        out.append(Finding(
            f"The picture carries camera details ({make.strip()} {model.strip()}).",
            "Image generators do not normally invent a camera make and model, so this is "
            "a point in the picture's favour. It does not prove the scene is real — a "
            "genuine camera can photograph a fake, and these details can be edited in.",
            "low", "picture"))
        pts -= 15

    if len(capture_fields) >= 3:
        out.append(Finding(
            "The picture carries the fine detail a camera records when it takes a photo.",
            "Shutter speed, aperture, lens and the moment it was taken are the kind of "
            "detail generators do not fabricate. Reassuring, but it can be copied from "
            "another photo.",
            "low", "picture"))
        pts -= 20

    if (img.width, img.height) in _GEN_CANVASES:
        out.append(Finding(
            f"The picture is exactly {img.width} by {img.height} pixels.",
            "That is one of the standard canvas sizes image generators produce. "
            "Screenshots, square crops and profile pictures land on these sizes too, so "
            "on its own this means almost nothing.",
            "medium", "picture"))
        pts += 12

    if not exif and not make and not capture_fields:
        out.append(Finding(
            "The picture carries no camera information at all.",
            "This is the weakest signal I have. WhatsApp, Facebook, Instagram and every "
            "screenshot strip this information out, so an ordinary holiday photo sent by "
            "a relative looks exactly like this. It only counts alongside something else.",
            "medium", "picture"))
        pts += 15

    return out, pts, False


# ═══════════════════════════════════════════════════════════════════════
# LAYER C — THE SECOND OPINION
# ═══════════════════════════════════════════════════════════════════════

_AI_MODEL = "gemini-2.5-flash"

_AI_SYSTEM = (
    "You help an older adult in Malaysia decide whether something is a scam, a "
    "phishing attempt, or content made by AI. Write for someone who is not "
    "technical and may be frightened. Every finding must say what you saw AND what "
    "it does not prove. Never claim certainty. Never tell them something is "
    "definitely safe. If nothing is wrong, return SAFE with an empty findings list. "
    "Keep each field short enough to read aloud."
)

_AI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "risk_band": {"type": "STRING", "enum": ["SAFE", "CAUTION", "DANGER"]},
        "findings": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "what": {"type": "STRING"},
                "why": {"type": "STRING"},
                "severity": {"type": "STRING", "enum": ["low", "medium", "high"]},
            },
            "required": ["what", "why", "severity"],
        }},
        "what_to_do": {"type": "ARRAY", "items": {"type": "STRING"}},
        "plain_summary": {"type": "STRING"},
        "ai_generated_likelihood": {"type": "STRING",
                                    "enum": ["unlikely", "unclear", "likely"]},
    },
    "required": ["risk_band", "findings", "what_to_do", "plain_summary"],
}


def _ask_gemini(part, prompt: str, timeout: float = 20.0) -> dict | None:
    """One-shot structured call on the vision key ladder. None on any failure.

    Deliberately the REST `generate_content` path, which returns text that can
    be rendered in a card — not `screen_processor.analyze_image`, which speaks
    its answer and returns only a boolean.
    """
    keys = [k for k in _get_vision_keys() if k]
    if not keys:
        return None

    from concurrent.futures import ThreadPoolExecutor
    from google import genai
    from google.genai import types as gtypes

    contents = [part, prompt] if part is not None else [prompt]

    def _one(api_key: str):
        client = genai.Client(api_key=api_key)
        return client.models.generate_content(
            model=_AI_MODEL,
            contents=contents,
            config=gtypes.GenerateContentConfig(
                system_instruction=_AI_SYSTEM,
                response_mime_type="application/json",
                response_schema=_AI_SCHEMA,
                temperature=0.0,
            ),
        )

    for key in keys[:2]:
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                resp = pool.submit(_one, key).result(timeout=timeout)
            data = json.loads(resp.text)
            if isinstance(data, dict) and data.get("risk_band"):
                return data
        except Exception as e:
            code = getattr(e, "code", None)
            print(f"[Safety] second opinion failed ({code or type(e).__name__}); "
                  f"{'trying next key' if code == 429 else 'continuing offline'}")
            if code != 429:
                return None
    return None


# ═══════════════════════════════════════════════════════════════════════
# BANDING, ADVICE, FUSION
# ═══════════════════════════════════════════════════════════════════════

def _band_for(score: int, image: bool) -> str:
    if image:
        return DANGER if score >= 40 else CAUTION if score >= 12 else SAFE
    return DANGER if score >= 55 else CAUTION if score >= 22 else SAFE


def _confidence(score: int, findings: list[Finding], degraded: bool) -> str:
    highs = sum(1 for f in findings if f.severity == "high")
    if highs >= 2 or score >= 70:
        level = "high"
    elif score >= 25 or len(findings) >= 3:
        level = "medium"
    else:
        level = "low"
    if degraded and not (highs >= 2):
        level = "medium" if level == "high" else level
    return level


# Fixed advice, never model-generated. Wrong advice is worse than no advice, and
# these four lines are correct for every scam this module can see.
_ADVICE = {
    DANGER: [
        "Do not click anything in it, and do not reply",
        "Never give anyone a one-time code, TAC or password — no bank, police officer "
        "or delivery company will ever ask",
        "Do not send money, buy gift cards or transfer to a “safe account”",
        "If it claims to be your bank, ring the number printed on your own bank card",
        "Report it to the National Scam Response Centre on 997",
    ],
    CAUTION: [
        "Do not click the link — open the website the way you normally do instead",
        "Read the sender's address slowly, letter by letter",
        "Ask someone you trust to look at it with you before you do anything",
        "If it is about money, ring the company on a number you already have",
    ],
    SAFE: [
        "Nothing obvious looked wrong, but stay careful",
        "Never give anyone a one-time code, TAC or password",
        "If money or urgency comes up later, stop and ask someone you trust",
    ],
}

# A picture needs different counsel from a message. "Do not click anything in
# it" is nonsense advice about a photograph, and advice that does not fit what
# the user is looking at teaches them to stop reading it.
_IMAGE_ADVICE = {
    DANGER: [
        "Do not treat this picture as proof that anything in it really happened",
        "If someone is using it to ask you for money or details, that is the part that matters",
        "Ask someone you trust to look at it with you",
        "Be especially careful if it shows someone you know asking for help",
    ],
    CAUTION: [
        "Be careful about believing what this picture shows",
        "See whether the same picture appears somewhere you already trust",
        "Ask someone you trust to look at it with you",
    ],
    SAFE: [
        "I found no signs it was made by AI, but I cannot be certain",
        "A real photograph can still be used to mislead you",
        "If it comes with a request for money or details, be careful anyway",
    ],
}

_HEADLINES = {
    "text": {
        DANGER: "This looks like a scam. I would not act on it.",
        CAUTION: "Some things about this are not right. Be careful.",
        SAFE: "I did not find anything obviously wrong with this.",
    },
    "image": {
        DANGER: "This picture says it was made by a computer, not a camera.",
        CAUTION: "There are some signs this picture may have been made by a computer.",
        SAFE: "This looks like an ordinary photograph. I found no signs it was made by AI.",
    },
}

# The badge. A picture judged artificial is not the same claim as a message
# judged fraudulent, even though both are shown in red — saying "THIS LOOKS
# LIKE A SCAM" over a holiday photo somebody generated would be simply wrong.
_CHIPS = {
    "text": {
        DANGER: "⛔  THIS LOOKS LIKE A SCAM",
        CAUTION: "⚠  BE CAREFUL",
        SAFE: "✓  NOTHING OBVIOUSLY WRONG",
    },
    "image": {
        DANGER: "⛔  LIKELY MADE BY AI",
        CAUTION: "⚠  MAY HAVE BEEN MADE BY AI",
        SAFE: "✓  NO SIGNS OF AI",
    },
}


def _finalise(kind: str, findings: list[Finding], score: int, degraded: bool,
              ai: dict | None, checked: list[str], image: bool) -> Verdict:
    score = max(0, min(100, score))
    det_band = _band_for(score, image)
    band = det_band

    if ai:
        ai_band = str(ai.get("risk_band", "")).upper()
        if ai_band in _ORDER:
            # The model may escalate by one step, never two, and never soften.
            # A hallucinated DANGER on a shopping list becomes CAUTION; a
            # missed scam still shows the offline engine's own verdict.
            capped = min(_ORDER[ai_band], _ORDER[det_band] + 1)
            band = _BY_RANK[max(_ORDER[det_band], capped)]
            if ai_band != det_band:
                findings = findings + [Finding(
                    "My offline checks and my online check did not fully agree.",
                    "I have gone with the more careful answer of the two.",
                    "low", "ai")]

        seen = {f.what[:40].lower() for f in findings}
        for raw in (ai.get("findings") or [])[:5]:
            what = str(raw.get("what", "")).strip()
            why = str(raw.get("why", "")).strip()
            if not what or what[:40].lower() in seen:
                continue
            seen.add(what[:40].lower())
            sev = str(raw.get("severity", "medium")).lower()
            findings.append(Finding(what, why, sev if sev in ("low", "medium", "high") else "medium", "ai"))

        if image and str(ai.get("ai_generated_likelihood", "")).lower() == "likely":
            findings.append(Finding(
                "Looking at the picture itself, it has the appearance of AI-generated work.",
                "This is a judgement about how the picture looks — lighting, hands, text, "
                "textures — not a measurement. It can be wrong in both directions.",
                "medium", "ai"))

    order = {"high": 0, "medium": 1, "low": 2}
    findings = sorted(findings, key=lambda f: order.get(f.severity, 1))

    advice = list(_IMAGE_ADVICE[band] if image else _ADVICE[band])
    if ai:
        for extra in (ai.get("what_to_do") or [])[:2]:
            extra = str(extra).strip()
            if extra and extra.lower() not in {a.lower() for a in advice}:
                advice.append(extra)

    conf = _confidence(score, findings, degraded)
    if ai and str(ai.get("risk_band", "")).upper() != det_band and conf == "high":
        conf = "medium"

    voice = "image" if image else "text"
    return Verdict(
        band=band, kind=kind, score=score, confidence=conf,
        headline=_HEADLINES[voice][band], chip=_CHIPS[voice][band],
        findings=findings, what_to_do=advice, checked=checked, degraded=degraded,
    )


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════

def _step(cb, msg: str):
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def check_text(text: str, *, use_ai: bool = True, on_step=None,
               kind: str = "text", extra: list[Finding] | None = None,
               extra_points: int = 0) -> Verdict:
    """Screen a message, an email body or a web address."""
    text = (text or "").strip()
    if not text:
        return Verdict(band=SAFE, kind=kind, headline="There was nothing for me to check.",
                       findings=[], what_to_do=list(_ADVICE[SAFE]), checked=[])

    findings = list(extra or [])
    score = extra_points
    checked = ["the wording"]

    _step(on_step, "Reading the words…")
    w_findings, w_points, categories = _analyse_wording(text)
    findings += w_findings
    score += w_points

    _step(on_step, "Checking the web addresses…")
    urls = extract_urls(text)
    login_wording = bool({"credential", "otp"} & categories)
    if urls:
        checked.append("the web addresses")
    for url in urls[:8]:
        u_findings, u_points = _analyse_url(url, login_wording)
        findings += u_findings
        score += u_points

    # A genuine notice uses one pressure tactic; a scam stacks several. This is
    # the rule that keeps "your payment is due immediately" out of the DANGER
    # band while still catching a message that is urgent AND threatening AND
    # asking for a code.
    if len(categories) > 2:
        bonus = min(30, 10 * (len(categories) - 2))
        score += bonus
        findings.append(Finding(
            f"The message uses {len(categories)} different pressure tactics at once.",
            "Any one of these can appear in a genuine message. Several together is the "
            "shape of a scam, because each one is there to stop you thinking about the "
            "last.",
            "high", "wording"))

    ai = None
    degraded = False
    if use_ai:
        _step(on_step, "Asking SIVA for a second opinion…")
        ai = _ask_gemini(None, _text_prompt(text))
        if ai:
            checked.append("SIVA's second opinion")
        else:
            degraded = True
    else:
        degraded = True

    return _finalise(kind, findings, score, degraded, ai, checked, image=False)


def check_image(src, *, kind: str = "image", use_ai: bool = True, on_step=None) -> Verdict:
    """Screen a picture or a screenshot: AI-generation markers and, if there is
    text in it, the scam wording that text carries."""
    if isinstance(src, (str, Path)):
        try:
            data = Path(src).read_bytes()
        except Exception as e:
            return Verdict(band=SAFE, kind=kind, degraded=True,
                           headline="I could not open that file.",
                           findings=[Finding("I could not read the file.", str(e), "low", "picture")],
                           what_to_do=list(_ADVICE[SAFE]), checked=[])
    else:
        data = bytes(src)

    _step(on_step, "Looking inside the picture…")
    findings, score, degraded = _image_forensics(data)
    checked = ["the picture's hidden details"]

    ai = None
    if use_ai:
        _step(on_step, "Asking SIVA to look at the picture…")
        try:
            from google.genai import types as gtypes
            mime = "image/png" if data[:8].startswith(b"\x89PNG") else "image/jpeg"
            part = gtypes.Part.from_bytes(data=data, mime_type=mime)
            ai = _ask_gemini(part, _image_prompt(kind))
        except Exception:
            ai = None
        if ai:
            checked.append("SIVA's second opinion")
        else:
            degraded = True
    else:
        degraded = True

    v = _finalise(kind, findings, score, degraded, ai, checked, image=True)

    # A scam screenshot is a scam first and a picture second. When the model
    # read words out of the image and judged them alarming, the wording verdict
    # is the one that matters to the person holding the phone.
    if ai and str(ai.get("risk_band", "")).upper() == DANGER:
        v.band = DANGER
        v.headline = _HEADLINES["text"][DANGER]
        v.chip = _CHIPS["text"][DANGER]
        v.what_to_do = list(_ADVICE[DANGER])
    return v


def check_file(path: str, *, use_ai: bool = True, on_step=None) -> Verdict:
    """Screen whatever kind of file this is."""
    p = Path(path)
    ext = p.suffix.lower().lstrip(".")

    if ext in {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "tif", "heic"}:
        return check_image(str(p), kind="image", use_ai=use_ai, on_step=on_step)

    if ext == "eml":
        _step(on_step, "Reading the email's hidden headers…")
        findings, points, body = _analyse_eml(str(p))
        v = check_text(body, use_ai=use_ai, on_step=on_step, kind="email",
                       extra=findings, extra_points=points)
        if "the sender's hidden details" not in v.checked:
            v.checked.insert(0, "the sender's hidden details")
        return v

    _step(on_step, "Reading the document…")
    if ext in {"txt", "md", "csv", "json", "html", "htm"}:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return Verdict(band=SAFE, kind="document", degraded=True,
                           headline="I could not read that file.",
                           findings=[Finding("I could not open the document.", str(e), "low")],
                           what_to_do=list(_ADVICE[SAFE]), checked=[])
        if ext in {"html", "htm"}:
            text = re.sub(r"<[^>]+>", " ", text)
    else:
        try:
            import sys
            sys.path.append(str(Path(__file__).resolve().parent.parent))
            from actions.file_processor import extract_text
            text = extract_text(str(p))
        except Exception as e:
            return Verdict(band=SAFE, kind="document", degraded=True,
                           headline="I could not read that kind of document.",
                           findings=[Finding(
                               "I could not get the words out of this file.",
                               f"{e}", "low")],
                           what_to_do=list(_ADVICE[SAFE]), checked=[])

    return check_text(text, use_ai=use_ai, on_step=on_step, kind="document")


def check(text: str | None = None, file_path: str | None = None,
          source: str = "auto", *, use_ai: bool = True, on_step=None) -> Verdict:
    """One door, for the tool dispatcher."""
    if file_path and (source in ("auto", "file") or not text):
        return check_file(file_path, use_ai=use_ai, on_step=on_step)
    return check_text(text or "", use_ai=use_ai, on_step=on_step)


def _text_prompt(text: str) -> str:
    return (
        "Decide whether the following message is a scam or phishing attempt, and whether "
        "it reads as if it was written by AI. Consider who it claims to be from, what it "
        "asks for, and how it pressures the reader.\n\n"
        f"--- MESSAGE ---\n{text[:12000]}\n--- END ---"
    )


def _image_prompt(kind: str) -> str:
    what = "screenshot" if kind == "screen" else "picture"
    return (
        f"Look at this {what}. Two questions.\n"
        "1. If it shows a message, an email, a website or a chat, is it a scam or phishing "
        "attempt? Read the sender, the web address and the wording.\n"
        "2. Does the image itself look AI-generated? Consider hands, faces, written text and "
        "letterforms, lighting and shadow direction, repeated textures, and warped background "
        "geometry. Say 'unclear' when you cannot tell — that is a valid answer.\n"
        "Set risk_band from the scam risk to the person, not from the image question."
    )
