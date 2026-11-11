"""Per-business posting log: which song went to which platform, and in what state.

Stored as ledger/<businessKey>.json in the media bucket. Videos are grouped by a
normalized "song key" derived from the file name, so Reel/Post/re-export variants
of one song land in the same record. Writes are read-modify-write guarded by S3
conditional writes (IfNoneMatch to create, IfMatch with the ETag to update).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable

from shared import media_queue

STATES = ("queued", "failed", "cancelled")
PLATFORMS = ("facebook", "instagram", "tiktok")
MAX_ATTEMPTS = 3
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


def mark(business_key: str, *, song_key: str, sha256: str, platform: str, state: str,
         scheduled_at: str | None, ref: str | None) -> None:
    if state not in STATES:
        raise ValueError(f"Unknown ledger state {state!r}.")
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown ledger platform {platform!r}.")
    client, bucket = media_queue._s3_client(), media_queue._bucket(None)
    for attempt in range(MAX_ATTEMPTS):
        data, etag = _read(business_key)
        song = data["songs"].setdefault(song_key, {"files": [], "platforms": {}})
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
            return
        except Exception as exc:  # noqa: BLE001
            if not _is_precondition_failure(exc) or attempt == MAX_ATTEMPTS - 1:
                raise


def run_and_mark(business_key: str, *, enabled: bool, file_path: str, sha256: str, platform: str,
                 scheduled_at: str, call: Callable[[], dict[str, Any]],
                 ref_of: Callable[[dict[str, Any]], str | None]) -> dict[str, Any]:
    """Run a schedule call, logging queued on success and failed (then re-raising) on error."""
    if not enabled:
        return call()
    key = song_key(os.path.basename(file_path))
    try:
        result = call()
    except Exception:
        mark(business_key, song_key=key, sha256=sha256, platform=platform, state="failed",
             scheduled_at=scheduled_at, ref=None)
        raise
    mark(business_key, song_key=key, sha256=sha256, platform=platform, state="queued",
         scheduled_at=scheduled_at, ref=ref_of(result))
    return result


def mark_cancelled(business_key: str, *, sha256: str | None, platform: str,
                   scheduled_at: str | None, ref: str | None) -> None:
    """Mark the platform cancelled for whichever song holds this sha; no-op if none does."""
    if not sha256:
        return
    key = song_for_sha(business_key, sha256)
    if key is None:
        return
    mark(business_key, song_key=key, sha256=sha256, platform=platform, state="cancelled",
         scheduled_at=scheduled_at, ref=ref)
