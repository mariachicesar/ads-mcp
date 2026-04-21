"""Google Ads MCP server — FastMCP protocol layer.

This wraps the existing REST tool logic and exposes it via the MCP protocol
so Claude Desktop and other MCP clients can use it directly.

Run via stdio (for Claude Desktop):
    python servers/google-ads/mcp_server.py

Required env vars:
    ADS_MCP_GOOGLE_ADS_CONFIGS_JSON  — JSON object keyed by businessKey
    ADS_MCP_REQUIRE_SIGNED_REQUESTS  — set to "false" for local dev
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from shared.models import ToolRequest
from tools.read import list_accounts, get_campaign_performance
from tools.write import update_campaign_budget

mcp = FastMCP(
    name="google-ads",
    instructions=(
        "Tools for managing Google Ads campaigns. "
        "Always use dry_run=true first for any write operation, "
        "show the proposed changes to the user, and only proceed with "
        "dry_run=false after explicit confirmation. "
        "Never touch the RnR Pasadena-San Marino campaign. "
        "Never change geo targeting for RnR until the USC Village move is confirmed."
    ),
)


@mcp.tool()
def google_ads_list_accounts(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
) -> dict:
    """List all Google Ads accounts accessible under the configured manager account."""
    req = ToolRequest(businessKey=business_key)
    return list_accounts(req, request_id=None)


@mcp.tool()
def google_ads_get_campaign_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
) -> dict:
    """Get campaign performance metrics (impressions, clicks, cost, conversions) for a business."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range})
    return get_campaign_performance(req, request_id=None)


@mcp.tool()
def google_ads_update_campaign_budget(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    campaign_name: Annotated[str, Field(description="Exact campaign name as it appears in Google Ads")],
    new_daily_budget: Annotated[float, Field(description="New daily budget in USD, e.g. 15.00")],
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
) -> dict:
    """Update the daily budget for a Google Ads campaign.

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(
        businessKey=business_key,
        dryRun=dry_run,
        payload={
            "campaignName": campaign_name,
            "newDailyBudget": new_daily_budget,
        },
    )
    return update_campaign_budget(req, request_id=None)


if __name__ == "__main__":
    mcp.run()
