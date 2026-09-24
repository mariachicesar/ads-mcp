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

from typing import Annotated, Callable

from fastmcp import FastMCP
from pydantic import Field

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tools.read import (
    get_ad_performance,
    get_adset_performance,
    get_audience_performance,
    get_campaign_performance,
    get_publisher_platform_breakdown,
    list_ad_accounts,
    list_creative_assets,
)

mcp = FastMCP(
    name="meta-ads",
    instructions=(
        "Tools for viewing Meta Ads Facebook and Instagram campaign performance. "
        "Instagram performance is returned through publisher platform breakdowns."
    ),
)


def _run_tool(tool_name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except AdsMcpError as exc:
        return exc.to_response(service="meta-ads", request_id=None)
    except Exception as exc:
        return AdsMcpError(status_code=500, error_code="INTERNAL_ERROR", message="Unhandled tool error.", tool=tool_name, details={"reason": str(exc)}).to_response(service="meta-ads", request_id=None)


def _performance_request(business_key: str, date_range: str, ids: list[str] | None, key: str) -> ToolRequest:
    payload: dict = {"dateRange": date_range}
    if ids:
        payload[key] = ids
    return ToolRequest(businessKey=business_key, payload=payload)


@mcp.tool()
def meta_ads_list_ad_accounts(business_key: Annotated[str, Field(description="Business key")]) -> dict:
    """Verify and retrieve the configured Meta Ads account."""
    return _run_tool("list_ad_accounts", lambda: list_ad_accounts(ToolRequest(businessKey=business_key), None))


@mcp.tool()
def meta_ads_get_campaign_performance(business_key: Annotated[str, Field(description="Business key")], date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS", campaign_ids: Annotated[list[str] | None, Field(description="Optional campaign IDs")] = None) -> dict:
    """Get campaign performance metrics from Meta Ads."""
    return _run_tool("get_campaign_performance", lambda: get_campaign_performance(_performance_request(business_key, date_range, campaign_ids, "campaignIds"), None))


@mcp.tool()
def meta_ads_get_adset_performance(business_key: Annotated[str, Field(description="Business key")], date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS", adset_ids: Annotated[list[str] | None, Field(description="Optional ad set IDs")] = None) -> dict:
    """Get ad set performance metrics from Meta Ads."""
    return _run_tool("get_adset_performance", lambda: get_adset_performance(_performance_request(business_key, date_range, adset_ids, "adsetIds"), None))


@mcp.tool()
def meta_ads_get_ad_performance(business_key: Annotated[str, Field(description="Business key")], date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS", ad_ids: Annotated[list[str] | None, Field(description="Optional ad IDs")] = None) -> dict:
    """Get ad performance metrics from Meta Ads."""
    return _run_tool("get_ad_performance", lambda: get_ad_performance(_performance_request(business_key, date_range, ad_ids, "adIds"), None))


@mcp.tool()
def meta_ads_get_publisher_platform_breakdown(business_key: Annotated[str, Field(description="Business key")], level: Annotated[str, Field(description="campaign, adset, or ad")] = "campaign", date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS") -> dict:
    """Compare Facebook, Instagram, Messenger, and Audience Network placement performance."""
    request = ToolRequest(businessKey=business_key, payload={"level": level, "dateRange": date_range})
    return _run_tool("get_publisher_platform_breakdown", lambda: get_publisher_platform_breakdown(request, None))


@mcp.tool()
def meta_ads_get_audience_performance(business_key: Annotated[str, Field(description="Business key")], level: Annotated[str, Field(description="campaign, adset, or ad")] = "campaign", date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS", include_country: Annotated[bool, Field(description="Include country in the audience breakdown")] = False) -> dict:
    """Get age and gender, optionally country, performance breakdowns."""
    request = ToolRequest(businessKey=business_key, payload={"level": level, "dateRange": date_range, "includeCountry": include_country})
    return _run_tool("get_audience_performance", lambda: get_audience_performance(request, None))


@mcp.tool()
def meta_ads_list_creative_assets(business_key: Annotated[str, Field(description="Business key")]) -> dict:
    """List creative assets already available in the Meta Ads account."""
    return _run_tool("list_creative_assets", lambda: list_creative_assets(ToolRequest(businessKey=business_key), None))


if __name__ == "__main__":
    mcp.run()
