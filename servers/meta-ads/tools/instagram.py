"""Instagram Reels scheduling through the S3 media queue.

Instagram's API has no native scheduling, so execute only uploads the video to
S3 and enqueues a post; scripts/run-scheduled-social.py publishes it on its
30-minute timer. No Instagram call happens at schedule time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shared import media_queue
from shared.caption_rules import check_caption
from shared.errors import AdsMcpError
from shared.meta_ads_client import check_instagram_publish_access, get_instagram_user_id
from shared.models import ToolRequest
from shared.responses import build_change, build_rule_check, build_success_response
from shared.runtime_config import load_meta_ads_config
from shared import social_ledger
from shared.social_profile import load_profile
from tools import publish
from tools.write import SERVICE_NAME, _invalid, _required, _write_flow

MAX_CAPTION_CHARS = 2200
MAX_HASHTAGS = 30
MAX_REEL_BYTES = 300_000_000  # Instagram Reels publishing limit (300 MB)
RUNNER_INTERVAL_MINUTES = 30
DEFAULT_LIST_STATES = ("pending", "processing", "failed")


def _caption(payload: dict[str, Any], tool: str) -> str:
    caption = str(payload.get("caption") or "").strip()
    if not caption:
        raise _invalid("caption is required.", tool)
    if len(caption) > MAX_CAPTION_CHARS:
        raise _invalid(f"caption is {len(caption)} characters; Instagram allows {MAX_CAPTION_CHARS}.", tool)
    hashtags = sum(1 for word in caption.split() if word.startswith("#"))
    if hashtags > MAX_HASHTAGS:
        raise _invalid(f"caption has {hashtags} hashtags; Instagram allows {MAX_HASHTAGS}.", tool)
    return caption


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    row = {k: v for k, v in entry.items() if not k.startswith("_")}
    when = datetime.strptime(entry["scheduledAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=publish.timezone.utc)
    return {**row, "state": entry["_state"], "scheduledPacific": when.astimezone(publish.SCHEDULE_TZ).isoformat()}


def schedule_instagram_reel(request: ToolRequest, request_id: str | None) -> dict:
    tool = "schedule_instagram_reel"
    payload = request.payload
    if payload.get("fileUrl"):
        raise _invalid("Instagram scheduling needs a local filePath (the video is stored in S3 until it posts).", tool)
    caption = _caption(payload, tool)
    when = publish._parse_schedule_time(_required(payload, "scheduledTime", tool), tool)
    source, sha = publish._video_source(payload, tool)
    if source["bytes"] > MAX_REEL_BYTES:
        raise _invalid(f"Video is {source['bytes']:,} bytes; Instagram Reels are limited to 300 MB.", tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    try:
        ig_user_id = get_instagram_user_id(config, page_id=str(config.get("page_id")), tool=tool)
    except AdsMcpError as exc:
        ig_user_id, linked_ok, linked_msg = None, False, f"Could not look up the Page's Instagram account: {exc.message}"
    else:
        if ig_user_id:
            linked_ok, linked_msg, _ = check_instagram_publish_access(config, ig_user_id=ig_user_id, tool=tool)
        else:
            linked_ok, linked_msg = False, "No Instagram professional account is linked to the configured Page (or the token lacks instagram_basic)."
    rule_checks = [
        build_rule_check(rule="instagram-linked", passed=linked_ok, severity="info" if linked_ok else "blocking",
                         message=linked_msg),
        publish._schedule_window_check(when),
    ]
    profile = load_profile(request.businessKey)
    if profile is not None:
        rule_checks.extend(check_caption(profile, caption))
    entry = {
        "platform": "instagram", "businessKey": request.businessKey,
        "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "videoSha256": sha,
        "caption": caption, "options": {"shareToFeed": True}, "igUserId": ig_user_id,
    }
    file = publish._file_preview(source, sha)
    changes = [build_change(field="instagram_reel", label="Scheduled Instagram Reel", before=None,
                            after={**entry, "file": file}, resource_type="queue_entry")]

    def execute() -> dict[str, Any]:
        def enqueue() -> dict[str, Any]:
            video_key = media_queue.put_video(business_key=request.businessKey, file_path=source["filePath"], sha256=sha)
            queue_key = media_queue.enqueue({**entry, "videoKey": video_key})
            return {"entryId": queue_key.rsplit("-", 1)[-1].removesuffix(".json"), "queueKey": queue_key, "videoKey": video_key}

        return social_ledger.run_and_mark(
            request.businessKey, enabled=profile is not None, file_path=source["filePath"], sha256=sha,
            platform="instagram", scheduled_at=entry["scheduledAt"], call=enqueue, ref_of=lambda r: r["entryId"])

    described = publish._describe_time(when)
    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"file": file, "scheduledFor": described, "entry": entry},
        dry_summary=(f"Would schedule an Instagram Reel for {described}; it publishes up to "
                     f"{RUNNER_INTERVAL_MINUTES} minutes after that time."),
        done_summary=f"Queued an Instagram Reel for {described}.",
        execute=execute, bound_args=publish._bound(request, sha),
    )


def _find_entry(business_key: str, entry_id: str, tool: str) -> dict[str, Any]:
    for entry in media_queue.list_entries(states=media_queue.STATES, business_key=business_key):
        if entry.get("id") == entry_id:
            return entry
    raise _invalid(f"No scheduled post with id {entry_id}.", tool)


def cancel_scheduled_social(request: ToolRequest, request_id: str | None) -> dict:
    tool = "cancel_scheduled_social"
    entry_id = str(_required(request.payload, "entryId", tool))
    entry = _find_entry(request.businessKey, entry_id, tool)
    pending = entry["_state"] == "pending"
    rule_checks = [build_rule_check(
        rule="entry-is-pending", passed=pending, severity="info" if pending else "blocking",
        message=(f"Entry {entry_id} is pending." if pending else
                 f"Entry {entry_id} is {entry['_state']}; only pending posts can be cancelled."),
    )]
    changes = [build_change(field="queue_entry.state", label="State", before=entry["_state"], after="cancelled",
                            resource_type="queue_entry", resource_id=entry_id)]
    def execute() -> dict[str, Any]:
        result = {"queueKey": media_queue.move(entry["_key"], "cancelled")}
        if load_profile(request.businessKey) is not None:
            social_ledger.mark_cancelled(request.businessKey, sha256=entry.get("videoSha256"),
                                         platform=entry.get("platform", "instagram"),
                                         scheduled_at=entry.get("scheduledAt"), ref=entry_id)
        return result

    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"entry": _public(entry)},
        dry_summary=f"Would cancel the {entry.get('platform')} post scheduled for {_public(entry)['scheduledPacific']}.",
        done_summary=f"Cancelled the {entry.get('platform')} post {entry_id}.",
        execute=execute,
    )


def list_scheduled_social(request: ToolRequest, request_id: str | None) -> dict:
    tool = "list_scheduled_social"
    states = tuple(request.payload.get("states") or DEFAULT_LIST_STATES)
    unknown = [s for s in states if s not in media_queue.STATES]
    if unknown:
        raise _invalid(f"Unknown states {unknown}; use {list(media_queue.STATES)}.", tool)
    entries = [_public(e) for e in media_queue.list_entries(states=states, business_key=request.businessKey)]
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"{len(entries)} scheduled social post(s) in states {list(states)}.", data={"entries": entries},
    )
