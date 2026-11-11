"""TikTok inbox scheduling through the S3 media queue.

Execute only uploads the video to S3 and enqueues an entry with platform
"tiktok"; scripts/run-scheduled-social.py sends it to the TikTok app inbox on
its 30-minute timer. Nothing here calls TikTok. Inbox drafts carry no caption,
so the caption is stored on the entry and shown by list_scheduled for the user
to paste when they tap Post.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from shared import media_queue, scheduling
from shared.caption_rules import check_caption
from shared.models import ToolRequest
from shared.responses import build_change, build_rule_check, build_success_response
from shared.social_profile import load_profile

SERVICE_NAME = "tiktok"
PLATFORM = "tiktok"
MAX_CAPTION_CHARS = 2200
DAILY_LIMIT = 5  # TikTok caps pending inbox shares per user per 24 h
DAILY_WINDOW = timedelta(hours=24)
RUNNER_INTERVAL_MINUTES = 30
DEFAULT_LIST_STATES = ("pending", "processing", "failed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _caption(payload: dict[str, Any], tool: str) -> str | None:
    caption = str(payload.get("caption") or "").strip()
    if len(caption) > MAX_CAPTION_CHARS:
        raise scheduling.invalid(f"caption is {len(caption)} characters; TikTok allows {MAX_CAPTION_CHARS}.", tool)
    return caption or None


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    row = {k: v for k, v in entry.items() if not k.startswith("_")}
    pacific = scheduling.queue_time(entry).astimezone(scheduling.SCHEDULE_TZ).isoformat()
    return {**row, "state": entry["_state"], "scheduledPacific": pacific}


def _daily_limit_check(business_key: str, when: datetime) -> dict[str, Any]:
    nearby = [
        e for e in media_queue.list_entries(states=("pending",), business_key=business_key, platform=PLATFORM)
        if not e.get("_invalid") and abs(scheduling.queue_time(e) - when) < DAILY_WINDOW
    ]
    ok = len(nearby) < DAILY_LIMIT
    return build_rule_check(
        rule="tiktok-daily-limit", passed=ok, severity="info" if ok else "blocking",
        message=(f"{len(nearby)} other TikTok draft(s) pending within 24 h of this one." if ok else
                 f"{len(nearby)} TikTok drafts are already pending within 24 h of this one; "
                 f"TikTok allows at most {DAILY_LIMIT} pending inbox shares per day."),
    )


def schedule_inbox_video(request: ToolRequest, request_id: str | None) -> dict:
    tool = "tiktok_schedule_inbox_video"
    payload = request.payload
    if payload.get("fileUrl"):
        raise scheduling.invalid("TikTok scheduling needs a local filePath (the video is stored in S3 until it is sent).", tool)
    caption = _caption(payload, tool)
    when = scheduling.parse_schedule_time(scheduling.required(payload, "scheduledTime", tool), tool)
    source, sha = scheduling.video_source(payload, tool)

    rule_checks = [scheduling.schedule_window_check(when, _now()), _daily_limit_check(request.businessKey, when)]
    profile = load_profile(request.businessKey)
    if profile is not None:
        rule_checks.extend(check_caption(profile, caption))
    entry = {
        "platform": PLATFORM, "businessKey": request.businessKey,
        "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "videoSha256": sha,
        "caption": caption, "options": {},
    }
    file = scheduling.file_preview(source, sha)
    changes = [build_change(field="tiktok_inbox_video", label="Scheduled TikTok inbox draft", before=None,
                            after={**entry, "file": file}, resource_type="queue_entry")]

    def execute() -> dict[str, Any]:
        video_key = media_queue.put_video(business_key=request.businessKey, file_path=source["filePath"], sha256=sha)
        queue_key = media_queue.enqueue({**entry, "videoKey": video_key})
        return {"entryId": queue_key.rsplit("-", 1)[-1].removesuffix(".json"), "queueKey": queue_key, "videoKey": video_key}

    described = scheduling.describe_time(when)
    caption_note = ("The caption is NOT attached to the draft — paste it from tiktok_list_scheduled when you post."
                    if caption else "No caption saved.")
    return scheduling.write_flow(
        request, request_id, service=SERVICE_NAME, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"file": file, "scheduledFor": described, "entry": entry},
        dry_summary=(f"Would send a video to the TikTok app inbox as a draft at {described} (up to "
                     f"{RUNNER_INTERVAL_MINUTES} minutes later); you then tap Post in TikTok. {caption_note}"),
        done_summary=f"Queued a TikTok inbox draft for {described}. {caption_note}",
        execute=execute, bound=scheduling.bound_args(request, sha),
    )


def _find_entry(business_key: str, entry_id: str, tool: str) -> dict[str, Any]:
    for entry in media_queue.list_entries(states=media_queue.STATES, business_key=business_key, platform=PLATFORM):
        if entry.get("id") == entry_id:
            return entry
    raise scheduling.invalid(f"No scheduled TikTok post with id {entry_id}.", tool)


def cancel_scheduled(request: ToolRequest, request_id: str | None) -> dict:
    tool = "tiktok_cancel_scheduled"
    entry_id = str(scheduling.required(request.payload, "entryId", tool))
    entry = _find_entry(request.businessKey, entry_id, tool)
    pending = entry["_state"] == "pending"
    rule_checks = [build_rule_check(
        rule="entry-is-pending", passed=pending, severity="info" if pending else "blocking",
        message=(f"Entry {entry_id} is pending." if pending else
                 f"Entry {entry_id} is {entry['_state']}; only pending posts can be cancelled."),
    )]
    changes = [build_change(field="queue_entry.state", label="State", before=entry["_state"], after="cancelled",
                            resource_type="queue_entry", resource_id=entry_id)]
    public = _public(entry)
    return scheduling.write_flow(
        request, request_id, service=SERVICE_NAME, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"entry": public},
        dry_summary=f"Would cancel the TikTok draft scheduled for {public['scheduledPacific']}.",
        done_summary=f"Cancelled the TikTok draft {entry_id}.",
        execute=lambda: {"queueKey": media_queue.move(entry["_key"], "cancelled")},
    )


def list_scheduled(request: ToolRequest, request_id: str | None) -> dict:
    tool = "tiktok_list_scheduled"
    states = tuple(request.payload.get("states") or DEFAULT_LIST_STATES)
    unknown = [s for s in states if s not in media_queue.STATES]
    if unknown:
        raise scheduling.invalid(f"Unknown states {unknown}; use {list(media_queue.STATES)}.", tool)
    entries = [_public(e) for e in media_queue.list_entries(states=states, business_key=request.businessKey,
                                                            platform=PLATFORM) if not e.get("_invalid")]
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"{len(entries)} scheduled TikTok post(s) in states {list(states)}.", data={"entries": entries},
    )
