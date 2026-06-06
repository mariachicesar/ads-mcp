"""Publish locally queued GBP posts whose scheduled_time has passed.

Run this via cron or a systemd timer, e.g.:
    # every 15 minutes
    */15 * * * * /opt/ads-mcp/.venv/bin/python /opt/ads-mcp/scripts/run-scheduled-posts.py

Exit codes: 0 = success (even if nothing to publish), 1 = partial failure.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Naive scheduled times in the queue are interpreted as this timezone.
# Both businesses operate in Pacific time; EC2 itself runs in UTC.
DEFAULT_SCHEDULE_TZ = ZoneInfo("America/Los_Angeles")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("run-scheduled-posts")

SCHEDULE_FILE = ROOT / "servers" / "gbp" / "scheduled_posts.json"


def load_queue() -> list[dict]:
    if not SCHEDULE_FILE.exists():
        return []
    return json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))


def save_queue(queue: list[dict]) -> None:
    SCHEDULE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")


def publish(entry: dict) -> None:
    from shared.runtime_config import load_platform_runtime_config
    from servers.gbp.gbp_client import create_post

    business_key = entry["businessKey"]
    payload = entry["payload"]

    config = load_platform_runtime_config(
        platform="gbp",
        business_key=business_key,
        required_keys=("client_id", "client_secret", "refresh_token", "gbp_location_id"),
        tool="run-scheduled-posts",
    )

    result = create_post(
        config,
        summary=payload["summary"],
        topic_type=payload.get("topic_type", "STANDARD"),
        call_to_action_type=payload.get("call_to_action_type"),
        call_to_action_url=payload.get("call_to_action_url"),
        event_title=payload.get("event_title"),
        event_start_date=payload.get("event_start_date"),
        event_end_date=payload.get("event_end_date"),
        offer_coupon_code=payload.get("offer_coupon_code"),
        offer_redeem_online_url=payload.get("offer_redeem_online_url"),
        tool="run-scheduled-posts",
    )
    log.info("Published scheduled post id=%s business=%s resource=%s",
             entry["id"], business_key, result.get("resourceName"))


def main() -> int:
    queue = load_queue()
    if not queue:
        log.info("No scheduled posts in queue.")
        return 0

    now = datetime.now(timezone.utc)
    pending, remaining, failed = [], [], []

    for entry in queue:
        try:
            scheduled = datetime.fromisoformat(entry["scheduledTime"])
            # Treat naive datetimes as Pacific time, then convert to UTC for comparison.
            # Without this, EC2 (UTC) would interpret "09:00" as 09:00 UTC = 02:00 PT.
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=DEFAULT_SCHEDULE_TZ)
            scheduled = scheduled.astimezone(timezone.utc)
        except (KeyError, ValueError) as exc:
            log.warning("Skipping malformed entry id=%s: %s", entry.get("id"), exc)
            remaining.append(entry)
            continue

        if scheduled <= now:
            pending.append(entry)
        else:
            remaining.append(entry)

    if not pending:
        log.info("No posts due yet (%d pending in queue).", len(remaining))
        save_queue(remaining)
        return 0

    log.info("%d post(s) due for publishing.", len(pending))
    for entry in pending:
        try:
            publish(entry)
        except Exception as exc:
            log.error("Failed to publish id=%s: %s", entry.get("id"), exc)
            entry["lastError"] = str(exc)
            failed.append(entry)

    # Keep failed entries in queue (with error attached) for retry
    save_queue(remaining + failed)

    if failed:
        log.error("%d post(s) failed to publish and remain in queue.", len(failed))
        return 1

    log.info("All due posts published successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
