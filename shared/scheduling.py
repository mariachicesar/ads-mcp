"""Helpers shared by the servers that schedule social videos (meta-ads, tiktok).

Validation of local video files, schedule-time parsing (naive = Pacific), the
schedule-window rule check, and the dry-run → approvalId → execute flow with a
caller-chosen service name.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from shared.approval_store import consume_approval, issue_approval, verify_approval
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_rule_check, build_success_response

MAX_VIDEO_BYTES = 1_073_741_824  # 1 GiB
VIDEO_EXTENSIONS = {".mp4", ".mov"}
SCHEDULE_TZ = ZoneInfo("America/Los_Angeles")
MIN_LEAD = timedelta(minutes=10)
MAX_LEAD = timedelta(days=75)


def invalid(message: str, tool: str) -> AdsMcpError:
    return AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message=message, tool=tool)


def required(payload: dict[str, Any], key: str, tool: str) -> Any:
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise invalid(f"{key} is required.", tool)
    return value


def parse_schedule_time(value: str, tool: str) -> datetime:
    """ISO 8601 → aware UTC datetime; a naive value is read as America/Los_Angeles."""
    try:
        when = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise invalid(f"scheduledTime '{value}' is not ISO 8601 (e.g. 2026-11-12T12:00:00).", tool) from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=SCHEDULE_TZ)
    return when.astimezone(timezone.utc)


def describe_time(when: datetime) -> str:
    return f"{when.astimezone(SCHEDULE_TZ).strftime('%a %b %d %Y %I:%M %p %Z')} ({when.strftime('%Y-%m-%dT%H:%M')}Z)"


def schedule_window_check(when: datetime, now: datetime) -> dict[str, Any]:
    ok = now + MIN_LEAD <= when <= now + MAX_LEAD
    return build_rule_check(
        rule="schedule-window", passed=ok, severity="info" if ok else "blocking",
        message=(f"Publishes {describe_time(when)}." if ok else
                 f"{describe_time(when)} must be between 10 minutes and 75 days from now."),
    )


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def video_source(payload: dict[str, Any], tool: str, *, max_bytes: int = MAX_VIDEO_BYTES) -> tuple[dict[str, Any], str | None]:
    """Return ({"filePath": ...} | {"fileUrl": ...}, sha256 or None) after validating the source."""
    file_path, file_url = payload.get("filePath"), payload.get("fileUrl")
    if bool(file_path) == bool(file_url):
        raise invalid("Provide exactly one of filePath or fileUrl.", tool)
    if file_url:
        if not str(file_url).lower().startswith("https://"):
            raise invalid("fileUrl must be a public https:// URL.", tool)
        return {"fileUrl": str(file_url)}, None

    path = str(file_path)
    if not os.path.isfile(path):
        raise invalid(f"filePath {path} does not exist or is not a file.", tool)
    if os.path.splitext(path)[1].lower() not in VIDEO_EXTENSIONS:
        raise invalid(f"filePath must be one of {sorted(VIDEO_EXTENSIONS)}.", tool)
    size = os.path.getsize(path)
    if size > max_bytes:
        raise invalid(f"Video is {size} bytes; the limit is {max_bytes} bytes.", tool)
    return {"filePath": path, "bytes": size}, sha256_file(path)


def file_preview(source: dict[str, Any], sha: str | None) -> dict[str, Any]:
    if "fileUrl" in source:
        return {"url": source["fileUrl"]}
    return {"path": source["filePath"], "bytes": source["bytes"], "sha256": sha}


def bound_args(request: ToolRequest, sha: str | None) -> dict[str, Any] | None:
    """Bind an approval to the file's hash too, so editing the file after the dry run invalidates it."""
    return {**request.payload, "fileSha256": sha} if sha else None


def queue_time(entry: dict[str, Any]) -> datetime:
    return datetime.strptime(entry["scheduledAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def write_flow(
    request: ToolRequest, request_id: str | None, *, service: str, tool: str,
    rule_checks: list[dict[str, Any]], changes: list[dict[str, Any]],
    preview: dict[str, Any], dry_summary: str, done_summary: str,
    execute: Callable[[], dict[str, Any]], bound: dict[str, Any] | None = None,
) -> dict:
    """Dry run without approvalId (issues one unless blocked); execute with it."""
    approval_args = request.payload if bound is None else bound
    blocked = any(not check["passed"] for check in rule_checks)

    if not request.approvalId:
        return build_success_response(
            service=service, tool=tool, mode="dry-run", business_key=request.businessKey,
            request_id=request_id,
            summary=dry_summary if not blocked else f"Blocked by business rules: {dry_summary}",
            rule_checks=rule_checks, changes=changes, data=preview,
            requires_confirmation=not blocked, executed=False,
            approval=None if blocked else issue_approval(
                service=service, tool=tool, business_key=request.businessKey, args=approval_args,
            ),
        )

    verify_approval(approval_id=request.approvalId, service=service, tool=tool,
                    business_key=request.businessKey, args=approval_args)
    if blocked:
        raise AdsMcpError(status_code=400, error_code="BUSINESS_RULE_BLOCKED",
                          message="Execution blocked by business rules.", rule_checks=rule_checks, tool=tool)
    # Burn the approval before acting so a retry can never double-apply.
    consume_approval(request.approvalId)
    result = execute()
    for change in changes:
        change["status"] = "applied"
    return build_success_response(
        service=service, tool=tool, mode="execute", business_key=request.businessKey,
        request_id=request_id, summary=done_summary, rule_checks=rule_checks,
        changes=changes, data={**preview, "result": result}, executed=True,
    )
