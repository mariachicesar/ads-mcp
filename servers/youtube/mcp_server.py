"""YouTube MCP server — read-only channel, video and analytics data.

Run via stdio:
    python servers/youtube/mcp_server.py

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

mcp = FastMCP(
    name="youtube",
    instructions=(
        "Read-only YouTube tools: channel totals, recent videos with lifetime stats, and YouTube Analytics "
        "(views, watch time, likes, shares, subscribers) by day, traffic source, video, country or device. "
        "Analytics data lags about two days."
    ),
)

_BK = Annotated[str, Field(description="Business key for any onboarded client, e.g. 'el-cuis'")]


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


if __name__ == "__main__":
    mcp.run()
