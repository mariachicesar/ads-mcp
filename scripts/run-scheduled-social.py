"""Publish due scheduled social posts (Instagram Reels, TikTok inbox drafts) from the S3 queue.

Run by the ads-mcp-social systemd timer every 30 minutes:
    /usr/bin/flock -n /tmp/ads-mcp-social.lock /opt/ads-mcp/.venv/bin/python scripts/run-scheduled-social.py

Rules (see docs/superpowers/plans/2026-11-10-instagram-scheduled-reels.md):
- due = scheduledAt <= now; more than 24 h late → failed ("missed").
- claim pending → processing before calling the platform (no double posts).
- retryable platform error → back to pending, up to 3 attempts; otherwise failed.
- processing older than 1 h → failed, never auto-reposted.
- a failed entry is marked failed in the posting log (best effort) so the next run offers it again.

Exit codes: 0 = nothing failed this run, 1 = at least one post failed.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import media_queue, secrets, social_ledger, tiktok_client  # noqa: E402
from shared.config import get_settings  # noqa: E402
from shared.errors import AdsMcpError  # noqa: E402
from shared.instagram_publish import publish_reel  # noqa: E402
from shared.runtime_config import load_meta_ads_config  # noqa: E402
from shared.social_profile import load_profile  # noqa: E402

log = logging.getLogger("run-scheduled-social")

MAX_ATTEMPTS = 3
MISSED_AFTER = timedelta(hours=24)
STALE_PROCESSING_AFTER = timedelta(hours=1)
VIDEO_URL_TTL_SECONDS = 3600

Publisher = Callable[[dict[str, Any]], dict[str, Any]]


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_instagram_entry(entry: dict[str, Any]) -> dict[str, Any]:
    config = load_meta_ads_config(business_key=entry["businessKey"], tool="run-scheduled-social")
    video_url = media_queue.presigned_get(entry["videoKey"], expires=VIDEO_URL_TTL_SECONDS)
    return publish_reel(
        config, ig_user_id=entry["igUserId"], video_url=video_url, caption=entry["caption"],
        share_to_feed=bool((entry.get("options") or {}).get("shareToFeed", True)),
    )


def publish_tiktok_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Send the video to the TikTok app inbox as a draft (TikTok inbox drafts carry no caption)."""
    secret_id = f"/ads-mcp/{entry['businessKey']}/tiktok/config"
    config = secrets.get_secret(secret_id, get_settings())
    if not isinstance(config, dict) or not config.get("refresh_token"):
        raise AdsMcpError(status_code=500, error_code="INTERNAL_ERROR", tool="tiktok_publish", retryable=False,
                          message=f"TikTok is not connected for {entry['businessKey']}. {tiktok_client.REAUTH_HINT}")
    access_token = tiktok_client.refresh_access_token(config, secret_id=secret_id)
    key = entry["videoKey"]
    sent = tiktok_client.send_to_inbox(
        access_token, size=media_queue.object_size(key),
        read_range=lambda start, end: media_queue.read_range(key, start, end),
    )
    caption = entry.get("caption")
    note = "Sent to the TikTok app inbox as a draft; open TikTok and tap Post."
    if caption:
        note += f" Paste this caption: {caption}"
    return {**sent, "note": note}


class UnknownOutcome(Exception):
    """The post may or may not be live; fail the queue entry but leave the posting log "queued"."""


class CancelledByUser(Exception):
    """The user cancelled this post while the entry was waiting; nothing to verify."""


def verify_youtube_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Check that a scheduled YouTube video went public after its slot (Task 3 "verify" entry)."""
    from shared.youtube_client import youtube_status  # lazy: runner must start without the Google libs

    video_id, business_key = entry["videoId"], entry["businessKey"]
    last_attempt = int(entry.get("attempts") or 0) + 1 >= MAX_ATTEMPTS
    not_public = f"YouTube video {video_id} is not public after its slot; check YouTube Studio"
    try:
        status = youtube_status(business_key, video_id)
    except Exception as exc:  # noqa: BLE001 - could not check: retry, then give up without re-offering
        if last_attempt:
            raise UnknownOutcome(not_public) from exc
        raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool="youtube_verify", retryable=True,
                          message=f"could not check YouTube video {video_id}: {type(exc).__name__}: {exc}") from exc
    if status is None:
        if _youtube_cancelled(entry):
            raise CancelledByUser(f"YouTube video {video_id} was cancelled")
        raise UnknownOutcome(not_public)
    if status.get("privacyStatus") == "public":
        return {"videoId": video_id, "privacyStatus": "public"}
    if last_attempt:
        raise UnknownOutcome(not_public)
    raise AdsMcpError(status_code=409, error_code="NOT_READY", tool="youtube_verify", retryable=True,
                      message=f"YouTube video {video_id} is still {status.get('privacyStatus')}")


def _youtube_cancelled(entry: dict[str, Any]) -> bool:
    try:
        sha = entry.get("videoSha256")
        song = social_ledger.song_for_sha(entry["businessKey"], sha) if sha else None
        if song is None:
            return False
        record = social_ledger.load(entry["businessKey"])["songs"][song]["platforms"].get("youtube") or {}
        return record.get("state") == "cancelled"
    except Exception:  # noqa: BLE001 - unreadable log: treat as not cancelled (unknown outcome)
        log.exception("could not read the posting log for %s", entry.get("id"))
        return False


def _log_failed(entry: dict[str, Any]) -> None:
    """Mark this entry's platform failed in the posting log; never affects publishing.

    Only this entry's own record is replaced (expected_ref), so a newer queued post of the
    same song is never clobbered.
    """
    try:
        business_key, sha, platform = entry.get("businessKey"), entry.get("videoSha256"), entry.get("platform")
        if not business_key or not sha or platform not in social_ledger.PLATFORMS or load_profile(business_key) is None:
            return
        song = social_ledger.song_for_sha(business_key, sha)
        if song is None:
            return
        social_ledger.mark(business_key, song_key=song, sha256=sha, platform=platform, state="failed",
                           scheduled_at=entry.get("scheduledAt"), ref=entry.get("id"), expected_ref=entry.get("id") or "")
    except Exception:  # noqa: BLE001
        log.exception("could not record failed %s %s in the posting log", entry.get("platform"), entry.get("id"))


def _fail(key: str, entry: dict[str, Any], reason: str, counts: dict[str, int], *, definite: bool = True,
          **updates: Any) -> None:
    """Move the entry to failed. definite=False means the post may already be live (stale claim,
    unknown exception): the posting log then stays "queued" so the next scan never re-offers it."""
    media_queue.move(key, "failed", updates={"lastError": reason, **updates})
    counts["failed"] += 1
    log.error("failed %s %s (%s): %s", entry.get("platform"), entry.get("id"), entry.get("businessKey"), reason)
    if definite:
        _log_failed(entry)


def _reason(exc: AdsMcpError) -> str:
    """The most useful human-readable cause: Meta's userMessage/reason beats our wrapper message."""
    return str(exc.details.get("userMessage") or exc.details.get("reason") or exc.message)


def _handle_processing(entry: dict[str, Any], now: datetime, counts: dict[str, int]) -> None:
    claimed = entry.get("claimedAt")
    if claimed and now - _parse(claimed) > STALE_PROCESSING_AFTER:
        platform = {"instagram": "Instagram", "tiktok": "the TikTok inbox", "youtube": "YouTube Studio",
                                                   "gbp": "the Google Business Profile"}.get(entry.get("platform"), "the platform")
        _fail(entry["_key"], entry, f"stale processing; check {platform} before retrying", counts, definite=False)


def _handle_pending(entry: dict[str, Any], now: datetime, publishers: dict[str, Publisher], counts: dict[str, int]) -> None:
    due = _parse(entry["scheduledAt"])
    if due > now:
        return
    if now - due > MISSED_AFTER:
        _fail(entry["_key"], entry, "missed: more than 24h late", counts,
              definite=entry.get("platform") != "youtube")  # youtube: video already uploaded
        return
    try:
        key = media_queue.move(entry["_key"], "processing", updates={"claimedAt": _iso(datetime.now(timezone.utc))})
    except KeyError:
        counts["skipped"] += 1
        return

    attempts = int(entry.get("attempts") or 0) + 1
    publisher = publishers.get(entry.get("platform"))
    if publisher is None:
        _fail(key, entry, f"no publisher for platform {entry.get('platform')!r}", counts,
              definite=entry.get("platform") != "youtube", attempts=attempts)
        return
    try:
        result = publisher(entry)
    except CancelledByUser as exc:
        media_queue.move(key, "cancelled", updates={"attempts": attempts, "lastError": str(exc)})
        log.info("cancelled %s %s: %s", entry.get("platform"), entry.get("id"), exc)
        return
    except UnknownOutcome as exc:
        _fail(key, entry, str(exc), counts, definite=False, attempts=attempts)
        return
    except AdsMcpError as exc:
        reason = _reason(exc)
        if exc.retryable and attempts < MAX_ATTEMPTS:
            media_queue.move(key, "pending", updates={"attempts": attempts, "lastError": reason})
            counts["retried"] += 1
            log.warning("retry %s %s attempt %d: %s", entry.get("platform"), entry.get("id"), attempts, reason)
        else:
            _fail(key, entry, reason, counts, attempts=attempts, errorDetails=exc.details)
        return
    except Exception as exc:  # noqa: BLE001 - unknown state; never retry automatically
        _fail(key, entry, f"{type(exc).__name__}: {exc}", counts, definite=False, attempts=attempts)
        return

    media_queue.move(key, "done", updates={"attempts": attempts, "result": result, "publishedAt": _iso(now)})
    counts["published"] += 1
    log.info("published %s %s (%s): %s", entry.get("platform"), entry.get("id"), entry.get("businessKey"), result)


def _guarded(entry: dict[str, Any], counts: dict[str, int], handler: Callable[[], None]) -> None:
    """Run one entry's handler; a bad entry is failed or quarantined instead of stopping the run."""
    if entry.get("_invalid"):
        media_queue.quarantine(entry["_key"], f"unreadable entry: {entry['_invalid']}")
        counts["failed"] += 1
        log.error("quarantined unreadable entry %s: %s", entry["_key"], entry["_invalid"])
        return
    try:
        handler()
    except Exception as exc:  # noqa: BLE001
        log.exception("error handling %s", entry.get("_key"))
        try:
            _fail(entry["_key"], entry, f"runner error: {type(exc).__name__}: {exc}", counts, definite=False)
        except Exception:  # noqa: BLE001 - already moved, or S3 down; next run retries the cleanup
            log.exception("could not mark %s failed", entry.get("_key"))


def run_once(*, now: datetime, publishers: dict[str, Publisher]) -> dict[str, int]:
    counts = {"published": 0, "retried": 0, "failed": 0, "skipped": 0}
    for entry in media_queue.list_entries(states=("processing",)):
        _guarded(entry, counts, lambda e=entry: _handle_processing(e, now, counts))
    for entry in media_queue.list_entries(states=("pending",)):
        _guarded(entry, counts, lambda e=entry: _handle_pending(e, now, publishers, counts))
    return counts


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    counts = run_once(now=datetime.now(timezone.utc), publishers={"instagram": publish_instagram_entry,
                                                                  "tiktok": publish_tiktok_entry,
                                                                  "youtube": verify_youtube_entry})
    log.info("run complete: %s", counts)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
