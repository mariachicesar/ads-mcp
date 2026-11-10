"""Meta video publishing tools: ad-library uploads and scheduled Page videos/Reels.

Same contract as tools/write.py: a call without approvalId returns a dry-run
preview plus an approvalId; calling again with it executes. Approvals for local
files are bound to the file's SHA-256, so editing the file after the dry run
invalidates the approval. Facebook holds scheduled posts itself
(published=false + scheduled_publish_time), so nothing is queued here.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from shared.errors import AdsMcpError
from shared.meta_ads_client import (
    RUPLOAD_URL,
    _graph_request,
    configured_account_id,
    get_page_access_token,
    post_video,
    upload_reel_bytes,
)
from shared.models import ToolRequest
from shared.responses import build_change, build_rule_check, build_success_response
from shared.runtime_config import load_meta_ads_config
from tools.write import SERVICE_NAME, _invalid, _required, _write_flow

MAX_VIDEO_BYTES = 1_073_741_824  # 1 GiB: Meta's non-resumable upload limit
VIDEO_EXTENSIONS = {".mp4", ".mov"}
# Naive schedule times are Pacific, matching scripts/run-scheduled-posts.py.
SCHEDULE_TZ = ZoneInfo("America/Los_Angeles")
MIN_LEAD = timedelta(minutes=10)
MAX_LEAD = timedelta(days=75)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_schedule_time(value: str, tool: str) -> datetime:
    """ISO 8601 → aware UTC datetime; a naive value is read as America/Los_Angeles."""
    try:
        when = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise _invalid(f"scheduledTime '{value}' is not ISO 8601 (e.g. 2026-11-12T12:00:00).", tool) from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=SCHEDULE_TZ)
    return when.astimezone(timezone.utc)


def _describe_time(when: datetime) -> str:
    return f"{when.astimezone(SCHEDULE_TZ).strftime('%a %b %d %Y %I:%M %p %Z')} ({when.strftime('%Y-%m-%dT%H:%M')}Z)"


def _schedule_window_check(when: datetime) -> dict[str, Any]:
    now = _now()
    ok = now + MIN_LEAD <= when <= now + MAX_LEAD
    return build_rule_check(
        rule="schedule-window", passed=ok, severity="info" if ok else "blocking",
        message=(f"Publishes {_describe_time(when)}." if ok else
                 f"{_describe_time(when)} must be between 10 minutes and 75 days from now."),
    )


def _page_check(config: dict[str, Any], page_id: str) -> dict[str, Any]:
    expected = str(config.get("page_id"))
    ok = page_id == expected
    return build_rule_check(
        rule="page-is-configured", passed=ok, severity="info" if ok else "blocking",
        message=(f"Posts to the configured Page {expected}." if ok
                 else f"Page {page_id} is not the configured Page {expected}."),
    )


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _video_source(payload: dict[str, Any], tool: str) -> tuple[dict[str, Any], str | None]:
    """Return ({"filePath": ...} | {"fileUrl": ...}, sha256 or None) after validating the source."""
    file_path, file_url = payload.get("filePath"), payload.get("fileUrl")
    if bool(file_path) == bool(file_url):
        raise _invalid("Provide exactly one of filePath or fileUrl.", tool)
    if file_url:
        if not str(file_url).lower().startswith("https://"):
            raise _invalid("fileUrl must be a public https:// URL.", tool)
        return {"fileUrl": str(file_url)}, None

    path = str(file_path)
    if not os.path.isfile(path):
        raise _invalid(f"filePath {path} does not exist or is not a file.", tool)
    if os.path.splitext(path)[1].lower() not in VIDEO_EXTENSIONS:
        raise _invalid(f"filePath must be one of {sorted(VIDEO_EXTENSIONS)}.", tool)
    size = os.path.getsize(path)
    if size > MAX_VIDEO_BYTES:
        raise _invalid(f"Video is {size} bytes; the limit is {MAX_VIDEO_BYTES} (1 GiB).", tool)
    return {"filePath": path, "bytes": size}, _sha256(path)


def _file_preview(source: dict[str, Any], sha: str | None) -> dict[str, Any]:
    if "fileUrl" in source:
        return {"url": source["fileUrl"]}
    return {"path": source["filePath"], "bytes": source["bytes"], "sha256": sha}


def _bound(request: ToolRequest, sha: str | None) -> dict[str, Any] | None:
    return {**request.payload, "fileSha256": sha} if sha else None


def upload_ad_video(request: ToolRequest, request_id: str | None) -> dict:
    tool = "upload_ad_video"
    payload = request.payload
    name = _required(payload, "name", tool)
    source, sha = _video_source(payload, tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    account_id = configured_account_id(config, tool=tool)
    path = f"{account_id}/advideos"
    fields = {"name": name}
    file = _file_preview(source, sha)
    rule_checks = [build_rule_check(rule="account-ownership", passed=True, message=f"Uploads to the configured ad account {account_id}.")]
    changes = [build_change(field="advideo", label="New ad video", before=None, after={**fields, "file": file}, resource_type="advideo")]

    def execute() -> dict[str, Any]:
        result = post_video(config, path=path, fields=fields, file_path=source.get("filePath"),
                            file_url=source.get("fileUrl"), tool=tool)
        return {"videoId": result.get("id")}

    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"file": file, "graphRequest": {"method": "POST", "path": path, "fields": fields}},
        dry_summary=f"Would upload video '{name}' to the {account_id} ad Media library.",
        done_summary=f"Uploaded video '{name}' to the {account_id} ad Media library.",
        execute=execute, bound_args=_bound(request, sha),
    )


def schedule_page_video(request: ToolRequest, request_id: str | None) -> dict:
    tool = "schedule_page_video"
    payload = request.payload
    description = _required(payload, "description", tool)
    when = _parse_schedule_time(_required(payload, "scheduledTime", tool), tool)
    source, sha = _video_source(payload, tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    page_id = str(payload.get("pageId") or config.get("page_id"))
    path = f"{page_id}/videos"
    fields = {
        "title": payload.get("title"), "description": description,
        "published": False, "scheduled_publish_time": int(when.timestamp()),
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    file = _file_preview(source, sha)
    rule_checks = [_page_check(config, page_id), _schedule_window_check(when)]
    changes = [build_change(field="page_video", label="Scheduled Page video", before=None,
                            after={**fields, "file": file}, resource_type="page_video")]

    def execute() -> dict[str, Any]:
        page_token = get_page_access_token(config, page_id=page_id, tool=tool)
        result = post_video(config, path=path, fields=fields, file_path=source.get("filePath"),
                            file_url=source.get("fileUrl"), access_token=page_token, tool=tool)
        return {"videoId": result.get("id")}

    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"file": file, "scheduledFor": _describe_time(when),
                 "graphRequest": {"method": "POST", "path": path, "fields": fields}},
        dry_summary=f"Would schedule a video on Page {page_id} for {_describe_time(when)}.",
        done_summary=f"Scheduled a video on Page {page_id} for {_describe_time(when)}.",
        execute=execute, bound_args=_bound(request, sha),
    )


def schedule_page_reel(request: ToolRequest, request_id: str | None) -> dict:
    tool = "schedule_page_reel"
    payload = request.payload
    description = _required(payload, "description", tool)
    when = _parse_schedule_time(_required(payload, "scheduledTime", tool), tool)
    source, sha = _video_source(payload, tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    page_id = str(payload.get("pageId") or config.get("page_id"))
    path = f"{page_id}/video_reels"
    finish = {"upload_phase": "finish", "video_state": "SCHEDULED",
              "scheduled_publish_time": int(when.timestamp()), "description": description}
    file = _file_preview(source, sha)
    rule_checks = [_page_check(config, page_id), _schedule_window_check(when)]
    changes = [build_change(field="page_reel", label="Scheduled Page Reel", before=None,
                            after={**finish, "file": file}, resource_type="page_reel")]

    def execute() -> dict[str, Any]:
        page_token = get_page_access_token(config, page_id=page_id, tool=tool)
        start = _graph_request(config, "POST", path, data={"upload_phase": "start"}, access_token=page_token, tool=tool)
        video_id = str(start.get("video_id"))
        upload_reel_bytes(config, video_id=video_id, access_token=page_token,
                          file_path=source.get("filePath"), file_url=source.get("fileUrl"), tool=tool)
        try:
            done = _graph_request(config, "POST", path, data={**finish, "video_id": video_id},
                                  access_token=page_token, tool=tool)
        except AdsMcpError as exc:
            exc.details = {**exc.details, "videoId": video_id,
                           "note": "Reel bytes were uploaded but the reel was not scheduled."}
            raise
        return {"videoId": video_id, "finish": done}

    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={
            "file": file, "scheduledFor": _describe_time(when),
            "graphRequests": [
                {"step": 1, "method": "POST", "path": path, "fields": {"upload_phase": "start"}},
                {"step": 2, "method": "POST", "path": f"{RUPLOAD_URL}/<version>/<video_id from step 1>", "fields": file},
                {"step": 3, "method": "POST", "path": path, "fields": {**finish, "video_id": "<from step 1>"}},
            ],
        },
        dry_summary=f"Would schedule a Reel on Page {page_id} for {_describe_time(when)}.",
        done_summary=f"Scheduled a Reel on Page {page_id} for {_describe_time(when)}.",
        execute=execute, bound_args=_bound(request, sha),
    )


def list_scheduled_page_videos(request: ToolRequest, request_id: str | None) -> dict:
    tool = "list_scheduled_page_videos"
    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    page_id = str(request.payload.get("pageId") or config.get("page_id"))
    if page_id != str(config.get("page_id")):
        raise _invalid(f"Page {page_id} is not the configured Page {config.get('page_id')}.", tool)

    page_token = get_page_access_token(config, page_id=page_id, tool=tool)
    rows = _graph_request(config, "GET", f"{page_id}/videos", access_token=page_token, tool=tool, params={
        "fields": "id,title,description,scheduled_publish_time,published,created_time", "limit": "100",
    }).get("data", [])
    videos = [
        {**row, "scheduledPacific": datetime.fromtimestamp(int(row["scheduled_publish_time"]), SCHEDULE_TZ).isoformat()}
        for row in rows
        if row.get("published") is False and row.get("scheduled_publish_time")
    ]
    videos.sort(key=lambda v: int(v["scheduled_publish_time"]))
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"{len(videos)} scheduled video(s) on Page {page_id}.", data={"pageId": page_id, "videos": videos},
    )
