"""Meta video publishing tools: ad-library uploads and scheduled Page videos/Reels.

Same contract as tools/write.py: a call without approvalId returns a dry-run
preview plus an approvalId; calling again with it executes. Approvals for local
files are bound to the file's SHA-256, so editing the file after the dry run
invalidates the approval. Facebook holds scheduled posts itself
(published=false + scheduled_publish_time), so nothing is queued here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.caption_rules import check_caption
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
from shared import social_ledger
from shared.social_profile import load_profile
from tools.write import SERVICE_NAME, _invalid, _required, _write_flow

from shared import scheduling
from shared.scheduling import (  # noqa: F401  (re-exported for callers and tests)
    MAX_LEAD, MAX_VIDEO_BYTES, MIN_LEAD, SCHEDULE_TZ, VIDEO_EXTENSIONS,
)

# Helpers live in shared.scheduling so the tiktok server can use them too.
_parse_schedule_time = scheduling.parse_schedule_time
_describe_time = scheduling.describe_time
_sha256 = scheduling.sha256_file
_file_preview = scheduling.file_preview
_bound = scheduling.bound_args


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _video_source(payload: dict[str, Any], tool: str) -> tuple[dict[str, Any], str | None]:
    return scheduling.video_source(payload, tool, max_bytes=MAX_VIDEO_BYTES)


def _schedule_window_check(when: datetime) -> dict[str, Any]:
    return scheduling.schedule_window_check(when, _now())


def _page_check(config: dict[str, Any], page_id: str) -> dict[str, Any]:
    expected = str(config.get("page_id"))
    ok = page_id == expected
    return build_rule_check(
        rule="page-is-configured", passed=ok, severity="info" if ok else "blocking",
        message=(f"Posts to the configured Page {expected}." if ok
                 else f"Page {page_id} is not the configured Page {expected}."),
    )


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


def _mark_facebook(request: ToolRequest, profile: Any, source: dict[str, Any], sha: str | None,
                   when: datetime, call: Any) -> dict[str, Any]:
    """Run a Facebook schedule call and log it (only for profiled businesses)."""
    file_path = source.get("filePath")
    return social_ledger.run_and_mark(
        request.businessKey, enabled=profile is not None and bool(file_path), file_path=file_path or "",
        sha256=sha or "", platform="facebook",
        scheduled_at=when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        call=call, ref_of=lambda r: r.get("videoId"))


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
    profile = load_profile(request.businessKey)
    if profile is not None:
        rule_checks.extend(check_caption(profile, description))
    changes = [build_change(field="page_video", label="Scheduled Page video", before=None,
                            after={**fields, "file": file}, resource_type="page_video")]

    def execute() -> dict[str, Any]:
        def post() -> dict[str, Any]:
            page_token = get_page_access_token(config, page_id=page_id, tool=tool)
            result = post_video(config, path=path, fields=fields, file_path=source.get("filePath"),
                                file_url=source.get("fileUrl"), access_token=page_token, tool=tool)
            return {"videoId": result.get("id")}

        return _mark_facebook(request, profile, source, sha, when, post)

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
    profile = load_profile(request.businessKey)
    if profile is not None:
        rule_checks.extend(check_caption(profile, description))
    changes = [build_change(field="page_reel", label="Scheduled Page Reel", before=None,
                            after={**finish, "file": file}, resource_type="page_reel")]

    def execute() -> dict[str, Any]:
        return _mark_facebook(request, profile, source, sha, when, schedule)

    def schedule() -> dict[str, Any]:
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


def _graph_time(value: Any) -> datetime:
    """Graph returns scheduled_publish_time as Unix seconds or as '2026-11-12T20:00:00+0000'."""
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(int(value), timezone.utc)
    return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z")


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
    videos = []
    for row in rows:
        if row.get("published") is False and row.get("scheduled_publish_time"):
            when = _graph_time(row["scheduled_publish_time"])
            videos.append({**row, "scheduledPacific": when.astimezone(SCHEDULE_TZ).isoformat(), "_sort": when})
    videos.sort(key=lambda v: v.pop("_sort"))
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"{len(videos)} scheduled video(s) on Page {page_id}.", data={"pageId": page_id, "videos": videos},
    )
