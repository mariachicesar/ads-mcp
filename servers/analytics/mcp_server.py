"""GA4 Analytics MCP server — FastMCP protocol layer.

Wraps the Analytics service and exposes it via the MCP protocol for Claude Desktop.

Run via stdio (for Claude Desktop):
    python servers/analytics/mcp_server.py

Required env vars:
    ADS_MCP_REQUIRE_SIGNED_REQUESTS  — set to "false" for local dev
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
from tools.read import get_traffic_overview, get_top_pages
from tools.write import create_key_event, link_google_ads

mcp = FastMCP(
    name="analytics",
    instructions=(
        "Tools for viewing GA4 website analytics. "
        "Requires ga4_property_id in the business config (local-dev-config.json or Secrets Manager). "
        "business_key identifies any onboarded client (see clients/*.json) — not limited to a fixed list. "
        "Write tools (key events, Google Ads link) always run dry_run=true first and need explicit user approval before dry_run=false. "
    ),
)


def _run_tool(tool_name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except AdsMcpError as exc:
        return exc.to_response(service="analytics", request_id=None)
    except Exception as exc:
        return AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Unhandled tool error.",
            tool=tool_name,
            details={"reason": str(exc)},
        ).to_response(service="analytics", request_id=None)


@mcp.tool()
def analytics_get_traffic_overview(
    business_key: Annotated[str, Field(description="Business key for any onboarded client, e.g. 'rnr-electrician', 'gq-painting', 'el-cuis' — not limited to these examples.")],
    date_range: Annotated[str, Field(description="Date range: LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS")] = "LAST_30_DAYS",
) -> dict:
    """Get GA4 traffic overview: sessions, users, bounce rate, and conversions broken down by channel group."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range})
    return get_traffic_overview(req, request_id=None)


@mcp.tool()
def analytics_get_top_pages(
    business_key: Annotated[str, Field(description="Business key for any onboarded client, e.g. 'rnr-electrician', 'gq-painting', 'el-cuis' — not limited to these examples.")],
    date_range: Annotated[str, Field(description="Date range: LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS")] = "LAST_30_DAYS",
    limit: Annotated[int, Field(description="Number of pages to return", ge=1, le=50)] = 10,
) -> dict:
    """Get top landing pages ranked by sessions, with conversions and bounce rate."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range, "limit": limit})
    return get_top_pages(req, request_id=None)


@mcp.tool()
def analytics_create_key_event(
    business_key: Annotated[str, Field(description="Business key for any onboarded client, e.g. 'rnr-electrician', 'gq-painting', 'el-cuis' — not limited to these examples.")],
    event_name: Annotated[str, Field(description="GA4 event name to mark as a key event, e.g. 'booking_confirmed'")],
    counting_method: Annotated[str, Field(description="ONCE_PER_SESSION (one conversion per visit; recommended for leads) or ONCE_PER_EVENT")] = "ONCE_PER_SESSION",
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Mark a GA4 event as a key event so Google Ads can import it as a conversion.

    Requires the analytics.edit OAuth scope for execute.
    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(businessKey=business_key, dryRun=dry_run, approvalId=approval_id,
                      payload={"eventName": event_name, "countingMethod": counting_method})
    return _run_tool("analytics_create_key_event", lambda: create_key_event(req, request_id=None))


@mcp.tool()
def analytics_link_google_ads(
    business_key: Annotated[str, Field(description="Business key for any onboarded client, e.g. 'rnr-electrician', 'gq-painting', 'el-cuis' — not limited to these examples.")],
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Link the client's GA4 property to the Google Ads account in the same client's manifest.

    Takes no customer ID on purpose, so it can't link to another client's account.
    Requires the analytics.edit OAuth scope for execute.
    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(businessKey=business_key, dryRun=dry_run, approvalId=approval_id)
    return _run_tool("analytics_link_google_ads", lambda: link_google_ads(req, request_id=None))


if __name__ == "__main__":
    mcp.run()
