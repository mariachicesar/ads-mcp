"""Per-business posting log: which song went to which platform, and in what state.

Stored as ledger/<businessKey>.json in the media bucket. Videos are grouped by a
normalized "song key" derived from the file name, so Reel/Post/re-export variants
of one song land in the same record. Writes are read-modify-write guarded by S3
conditional writes (IfNoneMatch to create, IfMatch with the ETag to update).
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable

from shared import media_queue

STATES = ("queued", "failed", "cancelled")
PLATFORMS = ("facebook", "instagram", "tiktok")
MAX_ATTEMPTS = 3
log = logging.getLogger(__name__)
_TRAILING_WORDS = {"reel", "post", "clip", "ads"}


def song_key(file_name: str) -> str:
    """Normalize a video file name to a key shared by all variants of the same song."""
    stem = os.path.splitext(os.path.basename(file_name))[0]
    text = unicodedata.normalize("NFKD", stem.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s*\(\d+\)\s*$", "", text).strip()
    words = text.split()
    while len(words) > 1 and words[-1] in _TRAILING_WORDS:
        words.pop()
    if words:
        stripped = re.sub(r"\d+$", "", words[-1])
        words = words[:-1] + ([stripped] if stripped else [])
    key = re.sub(r"[^a-z0-9]", "", "".join(words))
    return key or re.sub(r"[^a-z0-9]", "", text)


def _key(business_key: str) -> str:
    return f"ledger/{business_key}.json"


def _is_precondition_failure(exc: Exception) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in {"PreconditionFailed", "412", "ConditionalRequestConflict"}


def _read(business_key: str) -> tuple[dict[str, Any], str | None]:
    client = media_queue._s3_client()
    try:
        resp = client.get_object(Bucket=media_queue._bucket(None), Key=_key(business_key))
    except Exception as exc:  # noqa: BLE001 - boto raises ClientError for a missing key
        if media_queue._is_missing(client, exc):
            return {"songs": {}}, None
        raise
    data = json.loads(resp["Body"].read())
    data.setdefault("songs", {})
    return data, resp.get("ETag")


def load(business_key: str) -> dict[str, Any]:
    return _read(business_key)[0]


def song_for_sha(business_key: str, sha256: str) -> str | None:
    for key, song in load(business_key)["songs"].items():
        if sha256 in song.get("files", []):
            return key
    return None


def _may_replace(existing: dict[str, Any] | None, state: str, expected_ref: str | None) -> bool:
    """Guard a live record: never let a stale mark overwrite another entry's state.

    With expected_ref, only a record with no ref or that same ref may be replaced (the caller is
    acting on that one entry). Without it, a failed mark never replaces a queued record.
    """
    if not existing:
        return True
    if expected_ref is not None:
        return existing.get("ref") in (None, "", expected_ref)
    return not (state == "failed" and existing.get("state") == "queued")


def mark(business_key: str, *, song_key: str, sha256: str, platform: str, state: str,
         scheduled_at: str | None, ref: str | None, expected_ref: str | None = None) -> bool:
    """Record a platform's state for a song; returns False if a guard kept the stored record."""
    if state not in STATES:
        raise ValueError(f"Unknown ledger state {state!r}.")
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown ledger platform {platform!r}.")
    client, bucket = media_queue._s3_client(), media_queue._bucket(None)
    for attempt in range(MAX_ATTEMPTS):
        data, etag = _read(business_key)
        song = data["songs"].setdefault(song_key, {"files": [], "platforms": {}})
        if not _may_replace(song["platforms"].get(platform), state, expected_ref):
            return False
        if sha256 not in song["files"]:
            song["files"].append(sha256)
        song["platforms"][platform] = {
            "state": state, "scheduledAt": scheduled_at, "sha256": sha256, "ref": ref,
            "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        condition = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        try:
            client.put_object(Bucket=bucket, Key=_key(business_key), ContentType="application/json",
                              Body=json.dumps(data, ensure_ascii=False).encode("utf-8"), **condition)
            return True
        except Exception as exc:  # noqa: BLE001
            if not _is_precondition_failure(exc) or attempt == MAX_ATTEMPTS - 1:
                raise


def run_and_mark(business_key: str, *, enabled: bool, file_path: str, sha256: str, platform: str,
                 scheduled_at: str, call: Callable[[], dict[str, Any]],
                 ref_of: Callable[[dict[str, Any]], str | None]) -> dict[str, Any]:
    """Run a schedule call, logging queued on success and failed (then re-raising) on error.

    The log is best effort: once the platform call succeeded, a log failure must not fail the tool
    (that would invite a double post), so it is reported as `ledgerWarning`. If the platform call
    fails, the original error is always the one re-raised.
    """
    if not enabled:
        return call()
    key = song_key(os.path.basename(file_path))
    try:
        result = call()
    except Exception:
        try:
            mark(business_key, song_key=key, sha256=sha256, platform=platform, state="failed",
                 scheduled_at=scheduled_at, ref=None)
        except Exception:  # noqa: BLE001 - never mask the platform error
            log.exception("Could not record failed %s post in the posting log", platform)
        raise
    try:
        mark(business_key, song_key=key, sha256=sha256, platform=platform, state="queued",
             scheduled_at=scheduled_at, ref=ref_of(result))
    except Exception as exc:  # noqa: BLE001
        log.exception("Could not record queued %s post in the posting log", platform)
        return {**result, "ledgerWarning": f"Post was scheduled but the posting log was not updated: {exc}"}
    return result


def mark_cancelled(business_key: str, *, sha256: str | None, platform: str,
                   scheduled_at: str | None, ref: str | None) -> str | None:
    """Mark the platform cancelled for whichever song holds this sha; no-op if none does.

    Only the record for this entry (same ref, or no ref) is marked, so cancelling an old entry
    never clobbers a newer queued post of the same song.

    Best effort: returns a warning message if the log could not be updated, else None.
    """
    if not sha256:
        return None
    try:
        key = song_for_sha(business_key, sha256)
        if key is None:
            return None
        mark(business_key, song_key=key, sha256=sha256, platform=platform, state="cancelled",
             scheduled_at=scheduled_at, ref=ref, expected_ref=ref or "")
    except Exception as exc:  # noqa: BLE001
        log.exception("Could not record cancelled %s post in the posting log", platform)
        return f"Post was cancelled but the posting log was not updated: {exc}"
    return None
