"""YouTube MCP server — channel, video and analytics data, plus scheduled uploads.

Run via stdio:
    python servers/youtube/local_stdio.py

The schedule tools upload from this machine and write the S3 media queue
(ADS_MCP_MEDIA_BUCKET); scripts/run-scheduled-social.py later confirms the
video went public.

Config: platform "youtube" (ADS_MCP_YOUTUBE_CONFIGS_JSON or Secrets Manager
/ads-mcp/<business>/youtube/config) with client_id, client_secret, refresh_token
and optional channel_id. Get the token with scripts/youtube-auth.py.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from typing import Annotated, Callable

from fastmcp import FastMCP
from pydantic import Field

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tools.read import SERVICE_NAME, get_analytics, get_channel_overview, get_recent_videos
from tools.schedule import cancel_scheduled, list_scheduled, schedule_video

mcp = FastMCP(
    name="youtube",
    instructions=(
        "YouTube tools: channel totals, recent videos with lifetime stats, and YouTube Analytics "
        "(views, watch time, likes, shares, subscribers) by day, traffic source, video, country or device. "
        "Analytics data lags about two days. youtube_schedule_video uploads a video now as private with "
        "publishAt = the slot, so YouTube makes it public on its own (no tap needed); "
        "youtube_list_scheduled shows the queued uploads and youtube_cancel_scheduled deletes one before "
        "its publish time. Write tools: call first WITHOUT approval_id to get a dry-run preview and an "
        "approvalId, show the preview to the user, and only call again with that approval_id and the exact "
        "same arguments after explicit confirmation. approvalIds expire after 15 minutes."
    ),
)

_BK = Annotated[str, Field(description="Business key for any onboarded client, e.g. 'el-cuis'")]
_APPROVAL = Annotated[str | None, Field(description="Omit for a dry run. To execute, pass the approvalId from the dry-run response with identical arguments (expires after 15 minutes).")]


def _run_tool(tool_name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except AdsMcpError as exc:
        return exc.to_response(service=SERVICE_NAME, request_id=None)
    except Exception as exc:
        return AdsMcpError(status_code=500, error_code="INTERNAL_ERROR", message="Unhandled tool error.", tool=tool_name,
                           details={"reason": str(exc)}).to_response(service=SERVICE_NAME, request_id=None)


@mcp.tool()
def youtube_get_channel_overview(business_key: _BK) -> dict:
    """Channel totals: subscribers, lifetime views and video count."""
    req = ToolRequest(businessKey=business_key, payload={})
    return _run_tool("youtube_get_channel_overview", lambda: get_channel_overview(req, None))


@mcp.tool()
def youtube_get_recent_videos(
    business_key: _BK,
    limit: Annotated[int, Field(description="Number of most recent uploads (1-50)", ge=1, le=50)] = 10,
) -> dict:
    """Most recent uploads with lifetime views, likes, comments and engagement rate."""
    req = ToolRequest(businessKey=business_key, payload={"limit": limit})
    return _run_tool("youtube_get_recent_videos", lambda: get_recent_videos(req, None))


@mcp.tool()
def youtube_get_analytics(
    business_key: _BK,
    date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_28_DAYS, LAST_30_DAYS or LAST_90_DAYS")] = "LAST_28_DAYS",
    dimension: Annotated[str, Field(description="Group by: day, traffic_source, video, country or device")] = "day",
) -> dict:
    """YouTube Analytics: views, watch minutes, avg view duration, likes, comments, shares, subscribers gained."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range, "dimension": dimension})
    return _run_tool("youtube_get_analytics", lambda: get_analytics(req, None))


def _write_request(business_key: str, approval_id: str | None, payload: dict) -> ToolRequest:
    return ToolRequest(
        businessKey=business_key, approvalId=approval_id, dryRun=approval_id is None,
        payload={k: v for k, v in payload.items() if v is not None},
    )


@mcp.tool()
def youtube_schedule_video(
    business_key: _BK,
    file_path: Annotated[str, Field(description="Local .mp4/.mov file readable by this server (max 1 GiB)")],
    scheduled_time: Annotated[str, Field(description="Publish time, ISO 8601; naive = America/Los_Angeles. 10 minutes to 75 days from now")],
    title: Annotated[str, Field(description="Video title: starts with the song name, max 100 characters, no < or >")],
    description: Annotated[str, Field(description="Video description (the bilingual caption, with the phone number); max 5,000 characters, no < or >")],
    approval_id: _APPROVAL = None,
) -> dict:
    """Upload a video to YouTube as private, scheduled to go public at scheduled_time.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "filePath": file_path, "scheduledTime": scheduled_time, "title": title, "description": description,
    })
    return _run_tool("youtube_schedule_video", lambda: schedule_video(request, None))


@mcp.tool()
def youtube_list_scheduled(business_key: _BK) -> dict:
    """List YouTube uploads the posting log has as queued, with their live YouTube status and publish time."""
    request = ToolRequest(businessKey=business_key, payload={})
    return _run_tool("youtube_list_scheduled", lambda: list_scheduled(request, None))


@mcp.tool()
def youtube_cancel_scheduled(
    business_key: _BK,
    video_id: Annotated[str, Field(description="YouTube videoId from youtube_list_scheduled")],
    approval_id: _APPROVAL = None,
) -> dict:
    """Delete a scheduled YouTube video that is still private and before its publish time.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {"videoId": video_id})
    return _run_tool("youtube_cancel_scheduled", lambda: cancel_scheduled(request, None))


if __name__ == "__main__":
    mcp.run()
