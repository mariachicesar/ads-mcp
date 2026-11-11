"""Social MCP server — read-only helpers for the schedule-social skill.

Run via stdio:
    python servers/social/local_stdio.py
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
from tools.read import SERVICE_NAME, find_new_videos, get_posting_log, next_open_slots

mcp = FastMCP(name="social", instructions="Read-only helpers for the schedule-social skill.")

_BK = Annotated[str, Field(description="Business key, e.g. 'el-cuis'")]


def _run_tool(tool_name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except AdsMcpError as exc:
        return exc.to_response(service=SERVICE_NAME, request_id=None)
    except Exception as exc:
        return AdsMcpError(status_code=500, error_code="INTERNAL_ERROR", message="Unhandled tool error.", tool=tool_name,
                           details={"reason": str(exc)}).to_response(service=SERVICE_NAME, request_id=None)


@mcp.tool()
def social_find_new_videos(
    business_key: _BK,
    folder: Annotated[str | None, Field(description="Folder to scan; defaults to the profile's ready folder")] = None,
) -> dict:
    """List videos created after the profile's baseline cutoff that still need posting, oldest first."""
    request = ToolRequest(businessKey=business_key, payload={"folder": folder} if folder else {})
    return _run_tool("social_find_new_videos", lambda: find_new_videos(request, None))


@mcp.tool()
def social_next_open_slots(
    business_key: _BK,
    weekday_time: Annotated[str | None, Field(description="Weekday slot time HH:MM on :00 or :30 inside the profile's weekday window")] = None,
) -> dict:
    """List open posting slots for the next week, skipping times already queued."""
    request = ToolRequest(businessKey=business_key, payload={"weekdayTime": weekday_time} if weekday_time else {})
    return _run_tool("social_next_open_slots", lambda: next_open_slots(request, None))


@mcp.tool()
def social_get_posting_log(business_key: _BK) -> dict:
    """Return the posting log: which song went to which platform, and in what state."""
    request = ToolRequest(businessKey=business_key, payload={})
    return _run_tool("social_get_posting_log", lambda: get_posting_log(request, None))


if __name__ == "__main__":
    mcp.run()
