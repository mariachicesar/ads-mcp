from __future__ import annotations

import os
import re
import unicodedata

from shared.responses import build_rule_check

_HASHTAG_RE = re.compile(r"(?<!\w)#\w+", re.UNICODE)
_TRAILING_WORDS = {"reel", "post", "clip", "ads"}
_COPY_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", "", stripped)


def song_name(file_name: str) -> str:
    """Display name of the song in a video file name: the stem minus trailing Reel/Post/clip/ads and "(n)"."""
    words = os.path.splitext(os.path.basename(file_name))[0].split()
    while True:
        text = _COPY_SUFFIX_RE.sub("", " ".join(words)).strip()
        words = text.split()
        if len(words) > 1 and words[-1].casefold() in _TRAILING_WORDS:
            words.pop()
            continue
        return text


def starts_with_song(text: str, file_name: str) -> bool:
    """True if `text` begins with the song name, ignoring case, accents and spacing."""
    song = _normalize(song_name(file_name))
    return bool(song) and _normalize(text).startswith(song)


def has_phone(text: str, phone: str) -> bool:
    """True if `text` contains `phone` (compared on its last 10 digits) in any common format."""
    want = re.sub(r"\D", "", phone or "")[-10:]
    if len(want) < 10:
        return False
    squeezed = re.sub(r"[\s.\-()]", "", text or "")
    return any(want in run for run in re.findall(r"\d+", squeezed))


def _check(rule: str, passed: bool, ok_msg: str, fail_msg: str) -> dict:
    return build_rule_check(
        rule=rule,
        passed=passed,
        message=ok_msg if passed else fail_msg,
        severity="info" if passed else "blocking",
    )


def check_caption(profile: dict, caption: str | None) -> list[dict]:
    text = caption or ""
    max_chars = int(profile.get("hookMaxChars", 150))

    # Hook: first non-empty line.
    hook = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not hook:
        hook_ok, hook_fail = False, "Caption is missing; it needs a hook as its first line."
    elif len(hook) > max_chars:
        hook_ok, hook_fail = False, f"Hook (first line) is {len(hook)} chars; max is {max_chars}."
    elif hook.startswith("#") or hook.lower().startswith("http"):
        hook_ok, hook_fail = False, "Hook (first line) must be text, not a hashtag or link."
    else:
        hook_ok, hook_fail = True, ""

    # Link.
    domain = re.escape(profile.get("websiteDomain", ""))
    # The domain must end the host: a path/query/fragment, whitespace, or end of text may follow,
    # optionally after sentence punctuation ("...elcuis.com." / "(...elcuis.com)").
    https_ok = bool(re.search(rf"https://(www\.)?{domain}(?=[/?#]|[.,)]*(?:\s|$))", text, re.IGNORECASE))
    has_http = bool(re.search(r"http://\S+", text, re.IGNORECASE))
    link_passed = https_ok and not has_http
    if has_http:
        link_fail = "Caption contains an http:// link; use https:// only."
    else:
        link_fail = f"Caption is missing an https:// link to {profile.get('websiteDomain')}."

    # Hashtags.
    has_tags = bool(_HASHTAG_RE.search(text))

    # City.
    norm = _normalize(text)
    cities = profile.get("serviceCities", [])
    city_ok = any(_normalize(c) in norm for c in cities)

    checks = [
        _check("caption-hook", hook_ok, "Caption hook is valid.", hook_fail),
        _check("caption-https-link", link_passed, "Caption has an https link to the business site.", link_fail),
        _check("caption-hashtags", has_tags, "Caption has hashtags.", "Caption is missing hashtags."),
        _check("caption-city", city_ok, "Caption mentions a service city.",
               "Caption does not mention any service city: " + ", ".join(cities) + "."),
    ]
    phone = profile.get("phone")
    if phone:
        checks.append(_check("phone", has_phone(text, phone), "Caption includes the business phone number.",
                             f"Caption is missing the business phone number {phone}."))
    return checks
