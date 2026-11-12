"""Scheduled Google Business Profile companion posts for social videos, through the S3 media queue.

GBP has no native scheduling. The dry run grabs one still frame from the local
video (ffmpeg) into the frame cache so the user can look at the photo first.
Execute uploads that JPEG to S3 and enqueues an entry with platform "gbp";
scripts/run-scheduled-social.py creates the post (one PHOTO, CALL button, the
site link line appended) at the slot. Nothing here calls Google.

The existing GQ queue (scheduled_posts.json / gbp_create_post) is separate and
untouched.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from shared import media_queue, scheduling, social_ledger, video_probe
from shared.caption_rules import check_hook, has_phone, looks_like_phone
from shared.models import ToolRequest
from shared.responses import build_change, build_rule_check, build_success_response
from shared.social_profile import load_profile

SERVICE_NAME = "gbp"
PLATFORM = "gbp"
MAX_POST_CHARS = 1500
LINK_LINE_BUDGET = 120  # room for the link line the runner appends
DEFAULT_FRAME_FRACTION = 0.25
RUNNER_INTERVAL_MINUTES = 30
DEFAULT_LIST_STATES = ("pending", "processing", "failed")
FFMPEG_NEEDED = "ffmpeg is needed to make the GBP photo"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _frames_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "ads-mcp", "frames")


def _frame_seconds(payload: dict[str, Any], tool: str) -> float | None:
    value = payload.get("frameSeconds")
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise scheduling.invalid(f"frameSeconds must be a number of seconds, got {value!r}.", tool) from exc
    if seconds < 0:
        raise scheduling.invalid("frameSeconds must be 0 or more.", tool)
    return seconds


def _frame(file_path: str, sha: str, requested: float | None) -> tuple[str | None, float | None, dict[str, Any]]:
    """(frame path or None, frame time, gbp-frame rule check). Reuses a frame already in the cache."""
    def check(passed: bool, message: str) -> dict[str, Any]:
        return build_rule_check(rule="gbp-frame", passed=passed, severity="info" if passed else "blocking",
                                message=message)

    info = video_probe.probe(file_path)
    if requested is None:
        if info is None:
            return None, None, check(False, f"{FFMPEG_NEEDED}: could not read the video length (ffprobe missing "
                                            "or failed). Install ffmpeg or pass frame_seconds.")
        at = round(info["seconds"] * DEFAULT_FRAME_FRACTION, 1)
    else:
        at = requested
        if info is not None and at >= info["seconds"]:
            return None, at, check(False, f"frame_seconds {at:g} is past the end of the "
                                          f"{info['seconds']:g}-second video.")
    out = os.path.join(_frames_dir(), f"{sha}-{at:g}.jpg")
    if not os.path.isfile(out):
        try:
            video_probe.extract_frame(file_path, out, at_seconds=at)
        except RuntimeError as exc:
            return None, at, check(False, f"{FFMPEG_NEEDED}: {exc}")
    return out, at, check(True, f"GBP photo is the frame at {at:g} s: {out}")


def _phone_check(profile: dict[str, Any] | None, summary: str) -> dict[str, Any]:
    phone = (profile or {}).get("phone")
    found = looks_like_phone(summary) or bool(phone and has_phone(summary, phone))
    return build_rule_check(
        rule="gbp-no-phone", passed=not found, severity="blocking" if found else "info",
        message=("GBP post text must not contain a phone number (Google rejects it; the CALL button dials the "
                 "listing's number). Remove it." if found else "GBP post text has no phone number."),
    )


def _length_check(summary: str) -> dict[str, Any]:
    total = len(summary) + LINK_LINE_BUDGET
    ok = total <= MAX_POST_CHARS
    return build_rule_check(
        rule="gbp-length", passed=ok, severity="info" if ok else "blocking",
        message=(f"{len(summary)} characters + {LINK_LINE_BUDGET} reserved for the link line = {total}; "
                 f"GBP allows {MAX_POST_CHARS}."
                 + ("" if ok else f" Shorten the text by {total - MAX_POST_CHARS} characters.")),
    )


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    row = {k: v for k, v in entry.items() if not k.startswith("_")}
    pacific = scheduling.queue_time(entry).astimezone(scheduling.SCHEDULE_TZ).isoformat()
    return {**row, "state": entry["_state"], "scheduledPacific": pacific}


def schedule_social_post(request: ToolRequest, request_id: str | None) -> dict:
    tool = "gbp_schedule_social_post"
    payload = request.payload
    if payload.get("fileUrl"):
        raise scheduling.invalid("GBP scheduling needs a local filePath (a photo is taken from the video).", tool)
    summary = str(scheduling.required(payload, "summary", tool)).strip()
    requested = _frame_seconds(payload, tool)
    when = scheduling.parse_schedule_time(scheduling.required(payload, "scheduledTime", tool), tool)
    source, sha = scheduling.video_source(payload, tool)
    frame_path, frame_at, frame_check = _frame(source["filePath"], sha, requested)

    profile = load_profile(request.businessKey)
    rule_checks = [scheduling.schedule_window_check(when, _now()), frame_check]
    if profile is not None:
        rule_checks.append(check_hook(profile, summary))
    rule_checks += [_phone_check(profile, summary), _length_check(summary)]

    entry = {
        "platform": PLATFORM, "businessKey": request.businessKey,
        "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "videoSha256": sha,
        "summary": summary, "songKey": social_ledger.song_key(os.path.basename(source["filePath"])),
    }
    file = scheduling.file_preview(source, sha)
    changes = [build_change(field="gbp_social_post", label="Scheduled Google Business Profile post", before=None,
                            after={**entry, "framePath": frame_path, "file": file}, resource_type="queue_entry")]

    def execute() -> dict[str, Any]:
        def enqueue() -> dict[str, Any]:
            image_key = media_queue.put_image(business_key=request.businessKey, file_path=frame_path,
                                              sha256=scheduling.sha256_file(frame_path))
            queue_key = media_queue.enqueue({**entry, "imageKey": image_key})
            return {"entryId": queue_key.rsplit("-", 1)[-1].removesuffix(".json"), "queueKey": queue_key,
                    "imageKey": image_key}

        return social_ledger.run_and_mark(
            request.businessKey, enabled=profile is not None, file_path=source["filePath"], sha256=sha,
            platform=PLATFORM, scheduled_at=entry["scheduledAt"], call=enqueue, ref_of=lambda r: r["entryId"])

    described = scheduling.describe_time(when)
    bound = {**(scheduling.bound_args(request, sha) or {}), "frameSeconds": frame_at}
    return scheduling.write_flow(
        request, request_id, service=SERVICE_NAME, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"file": file, "framePath": frame_path, "frameSeconds": frame_at, "scheduledFor": described,
                 "entry": entry},
        dry_summary=(f"Would post to Google Business Profile at {described} (up to {RUNNER_INTERVAL_MINUTES} "
                     f"minutes later) with the frame at {frame_at if frame_at is not None else '?'} s as the "
                     f"photo and a Call button. Photo preview: {frame_path or 'not available'}."),
        done_summary=f"Queued a Google Business Profile post for {described}.",
        execute=execute, bound=bound,
    )


def _find_entry(business_key: str, entry_id: str, tool: str) -> dict[str, Any]:
    for entry in media_queue.list_entries(states=media_queue.STATES, business_key=business_key, platform=PLATFORM):
        if entry.get("id") == entry_id:
            return entry
    raise scheduling.invalid(f"No scheduled GBP post with id {entry_id}.", tool)


def cancel_social_post(request: ToolRequest, request_id: str | None) -> dict:
    tool = "gbp_cancel_social_post"
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

    def execute() -> dict[str, Any]:
        result = {"queueKey": media_queue.move(entry["_key"], "cancelled")}
        if load_profile(request.businessKey) is not None:
            warning = social_ledger.mark_cancelled(request.businessKey, sha256=entry.get("videoSha256"),
                                                   platform=PLATFORM, scheduled_at=entry.get("scheduledAt"),
                                                   ref=entry_id)
            if warning:
                result["ledgerWarning"] = warning
        return result

    return scheduling.write_flow(
        request, request_id, service=SERVICE_NAME, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"entry": public},
        dry_summary=f"Would cancel the GBP post scheduled for {public['scheduledPacific']}.",
        done_summary=f"Cancelled the GBP post {entry_id}.",
        execute=execute,
    )


def list_social_posts(request: ToolRequest, request_id: str | None) -> dict:
    tool = "gbp_list_social_posts"
    states = tuple(request.payload.get("states") or DEFAULT_LIST_STATES)
    unknown = [s for s in states if s not in media_queue.STATES]
    if unknown:
        raise scheduling.invalid(f"Unknown states {unknown}; use {list(media_queue.STATES)}.", tool)
    entries = [_public(e) for e in media_queue.list_entries(states=states, business_key=request.businessKey,
                                                            platform=PLATFORM) if not e.get("_invalid")]
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"{len(entries)} scheduled GBP post(s) in states {list(states)}.", data={"entries": entries},
    )
