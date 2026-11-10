"""Publish due scheduled social posts (Instagram; TikTok later) from the S3 queue.

Run by the ads-mcp-social systemd timer every 30 minutes:
    /usr/bin/flock -n /tmp/ads-mcp-social.lock /opt/ads-mcp/.venv/bin/python scripts/run-scheduled-social.py

Rules (see docs/superpowers/plans/2026-11-10-instagram-scheduled-reels.md):
- due = scheduledAt <= now; more than 24 h late → failed ("missed").
- claim pending → processing before calling the platform (no double posts).
- retryable platform error → back to pending, up to 3 attempts; otherwise failed.
- processing older than 1 h → failed, never auto-reposted.

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

from shared import media_queue  # noqa: E402
from shared.errors import AdsMcpError  # noqa: E402
from shared.instagram_publish import publish_reel  # noqa: E402
from shared.runtime_config import load_meta_ads_config  # noqa: E402

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


def _fail(key: str, entry: dict[str, Any], reason: str, counts: dict[str, int], **updates: Any) -> None:
    media_queue.move(key, "failed", updates={"lastError": reason, **updates})
    counts["failed"] += 1
    log.error("failed %s %s (%s): %s", entry.get("platform"), entry.get("id"), entry.get("businessKey"), reason)


def run_once(*, now: datetime, publishers: dict[str, Publisher]) -> dict[str, int]:
    counts = {"published": 0, "retried": 0, "failed": 0, "skipped": 0}

    for entry in media_queue.list_entries(states=("processing",)):
        claimed = entry.get("claimedAt")
        if claimed and now - _parse(claimed) > STALE_PROCESSING_AFTER:
            _fail(entry["_key"], entry, "stale processing; check Instagram before retrying", counts)

    for entry in media_queue.list_entries(states=("pending",)):
        due = _parse(entry["scheduledAt"])
        if due > now:
            continue
        if now - due > MISSED_AFTER:
            _fail(entry["_key"], entry, "missed: more than 24h late", counts)
            continue
        try:
            key = media_queue.move(entry["_key"], "processing", updates={"claimedAt": _iso(now)})
        except KeyError:
            counts["skipped"] += 1
            continue

        attempts = int(entry.get("attempts") or 0) + 1
        publisher = publishers.get(entry.get("platform"))
        if publisher is None:
            _fail(key, entry, f"no publisher for platform {entry.get('platform')!r}", counts, attempts=attempts)
            continue
        try:
            result = publisher(entry)
        except AdsMcpError as exc:
            if exc.retryable and attempts < MAX_ATTEMPTS:
                media_queue.move(key, "pending", updates={"attempts": attempts, "lastError": exc.message})
                counts["retried"] += 1
                log.warning("retry %s %s attempt %d: %s", entry.get("platform"), entry.get("id"), attempts, exc.message)
            else:
                _fail(key, entry, exc.message, counts, attempts=attempts, errorDetails=exc.details)
            continue
        except Exception as exc:  # noqa: BLE001 - unknown state; never retry automatically
            _fail(key, entry, f"{type(exc).__name__}: {exc}", counts, attempts=attempts)
            continue

        media_queue.move(key, "done", updates={"attempts": attempts, "result": result, "publishedAt": _iso(now)})
        counts["published"] += 1
        log.info("published %s %s (%s): %s", entry.get("platform"), entry.get("id"), entry.get("businessKey"), result)

    return counts


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    counts = run_once(now=datetime.now(timezone.utc), publishers={"instagram": publish_instagram_entry})
    log.info("run complete: %s", counts)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
