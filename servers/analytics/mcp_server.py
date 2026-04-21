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

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from shared.models import ToolRequest
from tools.read import get_traffic_overview, get_top_pages

mcp = FastMCP(
    name="analytics",
    instructions=(
        "Tools for viewing GA4 website analytics. "
        "Requires ga4_property_id in the business config (local-dev-config.json or Secrets Manager). "
        "Always use business_key 'rnr-electrician' or 'gq-painting'."
    ),
)


@mcp.tool()
def analytics_get_traffic_overview(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range: LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS")] = "LAST_30_DAYS",
) -> dict:
    """Get GA4 traffic overview: sessions, users, bounce rate, and conversions broken down by channel group."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range})
    return get_traffic_overview(req, request_id=None)


@mcp.tool()
def analytics_get_top_pages(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range: LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS")] = "LAST_30_DAYS",
    limit: Annotated[int, Field(description="Number of pages to return", ge=1, le=50)] = 10,
) -> dict:
    """Get top landing pages ranked by sessions, with conversions and bounce rate."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range, "limit": limit})
    return get_top_pages(req, request_id=None)


if __name__ == "__main__":
    mcp.run()
