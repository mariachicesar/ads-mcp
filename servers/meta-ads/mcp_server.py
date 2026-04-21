"""Meta Ads MCP server — FastMCP protocol layer.

Wraps the Meta Ads service and exposes it via the MCP protocol for Claude Desktop.

Run via stdio (for Claude Desktop):
    python servers/meta-ads/mcp_server.py

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

mcp = FastMCP(
    name="meta-ads",
    instructions=(
        "Tools for viewing Meta Ads (Facebook/Instagram) campaign performance. "
        "This service is in early development — data returned is placeholder only."
    ),
)


@mcp.tool()
def meta_ads_get_campaign_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS'")] = "LAST_30_DAYS",
) -> dict:
    """Get Meta Ads campaign performance metrics (impressions, clicks, spend, ROAS).

    Note: Meta Ads integration is not yet fully implemented. Returns placeholder data.
    """
    from shared.responses import build_success_response
    return build_success_response(
        service="meta-ads",
        tool="get_campaign_performance",
        mode="read",
        business_key=business_key,
        request_id=None,
        summary="Placeholder Meta Ads performance response.",
        data={"rows": [], "dateRange": date_range, "note": "Meta Ads SDK integration not yet built."},
        freshness={"state": "placeholder"},
    )


if __name__ == "__main__":
    mcp.run()
