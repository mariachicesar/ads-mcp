"""Read-only helpers for the schedule-social skill: folder scan, open slots, posting log."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared import media_queue, scheduling, social_ledger, social_scan, social_slots
from shared.models import ToolRequest
from shared.responses import build_success_response
from shared.social_profile import load_profile

SERVICE_NAME = "social"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _profile(business_key: str, tool: str) -> dict[str, Any]:
    profile = load_profile(business_key)
    if profile is None:
        raise scheduling.invalid(f"no social profile for business {business_key!r}.", tool)
    return profile


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _live_queue(business_key: str) -> list[dict[str, Any]]:
    """Queue entries that will still post: pending or processing."""
    return media_queue.list_entries(states=("pending", "processing"), business_key=business_key)


def find_new_videos(request: ToolRequest, request_id: str | None) -> dict:
    tool = "social_find_new_videos"
    profile = _profile(request.businessKey, tool)
    ledger = social_ledger.load(request.businessKey)
    queued = [e for e in _live_queue(request.businessKey) if not e.get("_invalid")]
    try:
        result = social_scan.find_new_videos(profile, ledger, folder=request.payload.get("folder"), queue_entries=queued)
    except OSError as exc:
        raise scheduling.invalid(f"Cannot read the folder: {exc}", tool) from exc
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"{len(result['candidates'])} new video(s); {result['baselineCount']} older file(s) ignored.",
        data=result,
    )


def next_open_slots(request: ToolRequest, request_id: str | None) -> dict:
    tool = "social_next_open_slots"
    profile = _profile(request.businessKey, tool)
    taken = [
        _utc(p["scheduledAt"])
        for song in social_ledger.load(request.businessKey)["songs"].values()
        for p in song["platforms"].values()
        if p.get("state") == "queued" and p.get("scheduledAt")
    ]
    taken += [
        scheduling.queue_time(e)
        for e in _live_queue(request.businessKey)
        if not e.get("_invalid")
    ]
    try:
        slots = social_slots.open_slots(profile, now=_now(), taken=taken, weekday_time=request.payload.get("weekdayTime"))
    except ValueError as exc:
        raise scheduling.invalid(str(exc), tool) from exc
    rows = [{"utc": s.strftime("%Y-%m-%dT%H:%M:%SZ"), "pacific": s.astimezone(scheduling.SCHEDULE_TZ).isoformat()} for s in slots]
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"{len(rows)} open slot(s) in the next {profile['slots']['horizonDays']} days.", data={"slots": rows},
    )


def get_posting_log(request: ToolRequest, request_id: str | None) -> dict:
    tool = "social_get_posting_log"
    _profile(request.businessKey, tool)
    ledger = social_ledger.load(request.businessKey)
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"{len(ledger['songs'])} song(s) in the posting log.", data=ledger,
    )
