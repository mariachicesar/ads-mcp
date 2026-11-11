"""TikTok MCP server — schedule videos into the TikTok app inbox via the S3 queue,
and read account/video stats.

The schedule tools only touch S3 (scripts/run-scheduled-social.py talks to TikTok);
the read-only stats tools call TikTok's Display API directly.

Run via stdio:
    python servers/tiktok/local_stdio.py
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
from tools.analytics import get_account_stats, get_video_stats
from tools.schedule import SERVICE_NAME, cancel_scheduled, list_scheduled, schedule_inbox_video

mcp = FastMCP(
    name="tiktok",
    instructions=(
        "Tools for scheduling TikTok videos. At the scheduled time (up to 30 minutes later) the video "
        "arrives in the TikTok app inbox as a draft and the user taps Post; captions are not attached, "
        "so show the saved caption from tiktok_list_scheduled. Write tools: call first WITHOUT "
        "approval_id to get a dry-run preview and an approvalId, show the preview to the user, and only "
        "call again with that approval_id and the exact same arguments after explicit confirmation. "
        "approvalIds expire after 15 minutes. tiktok_get_account_stats and tiktok_get_video_stats are "
        "read-only and return lifetime counts (TikTok's API has no per-day views or traffic sources)."
    ),
)

_BK = Annotated[str, Field(description="Business key, e.g. 'el-cuis'")]
_APPROVAL = Annotated[str | None, Field(description="Omit for a dry run. To execute, pass the approvalId from the dry-run response with identical arguments (expires after 15 minutes).")]
_WHEN = Annotated[str, Field(description="ISO 8601 time; naive = America/Los_Angeles. 10 minutes to 75 days from now")]


def _run_tool(tool_name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except AdsMcpError as exc:
        return exc.to_response(service=SERVICE_NAME, request_id=None)
    except Exception as exc:
        return AdsMcpError(status_code=500, error_code="INTERNAL_ERROR", message="Unhandled tool error.", tool=tool_name,
                           details={"reason": str(exc)}).to_response(service=SERVICE_NAME, request_id=None)


def _write_request(business_key: str, approval_id: str | None, payload: dict) -> ToolRequest:
    return ToolRequest(
        businessKey=business_key, approvalId=approval_id, dryRun=approval_id is None,
        payload={k: v for k, v in payload.items() if v is not None},
    )


@mcp.tool()
def tiktok_schedule_inbox_video(
    business_key: _BK,
    file_path: Annotated[str, Field(description="Local .mp4/.mov file readable by this server (max 1 GiB)")],
    scheduled_time: _WHEN,
    caption: Annotated[str | None, Field(description="Caption to paste when posting (saved, not sent to TikTok; max 2,200 characters)")] = None,
    approval_id: _APPROVAL = None,
) -> dict:
    """Schedule a TikTok video to land in the TikTok app inbox as a draft at scheduled_time.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "filePath": file_path, "scheduledTime": scheduled_time, "caption": caption,
    })
    return _run_tool("tiktok_schedule_inbox_video", lambda: schedule_inbox_video(request, None))


@mcp.tool()
def tiktok_list_scheduled(
    business_key: _BK,
    states: Annotated[list[str] | None, Field(description="Queue states: pending, processing, done, failed, cancelled (default pending, processing, failed)")] = None,
) -> dict:
    """List scheduled TikTok drafts with their state, Pacific time and saved caption."""
    request = ToolRequest(businessKey=business_key, payload={"states": states} if states else {})
    return _run_tool("tiktok_list_scheduled", lambda: list_scheduled(request, None))


@mcp.tool()
def tiktok_cancel_scheduled(
    business_key: _BK,
    entry_id: Annotated[str, Field(description="Queue entry id from tiktok_list_scheduled")],
    approval_id: _APPROVAL = None,
) -> dict:
    """Cancel a pending scheduled TikTok draft.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {"entryId": entry_id})
    return _run_tool("tiktok_cancel_scheduled", lambda: cancel_scheduled(request, None))


@mcp.tool()
def tiktok_get_account_stats(business_key: _BK) -> dict:
    """Read-only TikTok account totals: followers, following, total likes and video count."""
    request = ToolRequest(businessKey=business_key, payload={})
    return _run_tool("tiktok_get_account_stats", lambda: get_account_stats(request, None))


@mcp.tool()
def tiktok_get_video_stats(
    business_key: _BK,
    days: Annotated[int | None, Field(description="Only videos posted in the last N days (1-365); omit for the latest videos", ge=1, le=365)] = None,
    limit: Annotated[int, Field(description="Max videos to fetch, newest first (1-100)", ge=1, le=100)] = 20,
) -> dict:
    """Read-only per-video TikTok stats (views, likes, comments, shares, engagement rate) with totals."""
    payload = {"limit": limit} if days is None else {"limit": limit, "days": days}
    request = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("tiktok_get_video_stats", lambda: get_video_stats(request, None))


if __name__ == "__main__":
    mcp.run()
