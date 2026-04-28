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
from typing import Annotated, Callable

from fastmcp import FastMCP
from pydantic import Field

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tools.read import (
    list_accounts,
    get_campaign_performance,
    list_campaigns,
    get_ad_group_performance,
    get_keyword_performance,
    get_search_terms_report,
    get_impression_share,
    get_ad_performance,
    get_device_performance,
    get_geo_performance,
    get_schedule_performance,
    get_audience_performance,
    get_conversion_actions,
    get_change_history,
    get_recommendations,
)
from tools.write import (
    update_campaign_budget,
    set_campaign_status,
    set_ad_group_status,
    add_negative_keyword,
    update_keyword_bid,
    update_ad_status,
    create_rsa,
    update_campaign_bidding_strategy,
)

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


def _run_tool(tool_name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except AdsMcpError as exc:
        return exc.to_response(service="google-ads", request_id=None)
    except Exception as exc:
        return AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Unhandled tool error.",
            tool=tool_name,
            details={"reason": str(exc)},
        ).to_response(service="google-ads", request_id=None)


@mcp.tool()
def google_ads_list_accounts(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
) -> dict:
    """List all Google Ads accounts accessible under the configured manager account."""
    req = ToolRequest(businessKey=business_key)
    return _run_tool("google_ads_list_accounts", lambda: list_accounts(req, request_id=None))


@mcp.tool()
def google_ads_get_campaign_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
) -> dict:
    """Get campaign performance metrics (impressions, clicks, cost, conversions) for a business."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range})
    return _run_tool("google_ads_get_campaign_performance", lambda: get_campaign_performance(req, request_id=None))


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
    return _run_tool("google_ads_update_campaign_budget", lambda: update_campaign_budget(req, request_id=None))


@mcp.tool()
def google_ads_list_campaigns(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
) -> dict:
    """List all campaigns with status, daily budget, bidding strategy, and dates."""
    req = ToolRequest(businessKey=business_key)
    return _run_tool("google_ads_list_campaigns", lambda: list_campaigns(req, request_id=None))


@mcp.tool()
def google_ads_get_ad_group_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
    campaign_name: Annotated[str | None, Field(description="Optional: filter to a specific campaign name")] = None,
) -> dict:
    """Get performance metrics (impressions, clicks, cost, conversions, CTR, avg CPC) broken down by ad group."""
    payload: dict = {"dateRange": date_range}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("google_ads_get_ad_group_performance", lambda: get_ad_group_performance(req, request_id=None))


@mcp.tool()
def google_ads_get_keyword_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
    campaign_name: Annotated[str | None, Field(description="Optional: filter to a specific campaign name")] = None,
) -> dict:
    """Get keyword-level performance including Quality Score, match type, impressions, clicks, cost, and avg CPC."""
    payload: dict = {"dateRange": date_range}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("google_ads_get_keyword_performance", lambda: get_keyword_performance(req, request_id=None))


@mcp.tool()
def google_ads_get_search_terms_report(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
    campaign_name: Annotated[str | None, Field(description="Optional: filter to a specific campaign name")] = None,
) -> dict:
    """Get the search terms report — actual queries users typed that triggered your ads. Essential for finding new keywords and negative keywords."""
    payload: dict = {"dateRange": date_range}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("google_ads_get_search_terms_report", lambda: get_search_terms_report(req, request_id=None))


@mcp.tool()
def google_ads_set_campaign_status(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    campaign_name: Annotated[str, Field(description="Exact campaign name as it appears in Google Ads")],
    status: Annotated[str, Field(description="New status: 'ENABLED' or 'PAUSED'")],
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
) -> dict:
    """Pause or enable a Google Ads campaign.

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    Never pause the RnR Pasadena-San Marino campaign.
    """
    req = ToolRequest(
        businessKey=business_key,
        dryRun=dry_run,
        payload={"campaignName": campaign_name, "status": status},
    )
    return _run_tool("google_ads_set_campaign_status", lambda: set_campaign_status(req, request_id=None))


@mcp.tool()
def google_ads_set_ad_group_status(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    ad_group_name: Annotated[str, Field(description="Exact ad group name as it appears in Google Ads")],
    status: Annotated[str, Field(description="New status: 'ENABLED' or 'PAUSED'")],
    campaign_name: Annotated[str | None, Field(description="Optional: campaign name to disambiguate if ad group name is not unique")] = None,
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
) -> dict:
    """Pause or enable a Google Ads ad group.

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    Never pause the RnR NoHo EV Charge ad group.
    """
    payload: dict = {"adGroupName": ad_group_name, "status": status}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, dryRun=dry_run, payload=payload)
    return _run_tool("google_ads_set_ad_group_status", lambda: set_ad_group_status(req, request_id=None))


@mcp.tool()
def google_ads_get_impression_share(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
) -> dict:
    """Get search impression share, budget lost IS, rank lost IS, and absolute top IS by campaign. Essential for diagnosing visibility gaps."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range})
    return _run_tool("google_ads_get_impression_share", lambda: get_impression_share(req, request_id=None))


@mcp.tool()
def google_ads_get_ad_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
    campaign_name: Annotated[str | None, Field(description="Optional: filter to a specific campaign name")] = None,
) -> dict:
    """Get individual ad performance including headlines, descriptions, CTR, conversions, and cost. Use to identify top/bottom performers."""
    payload: dict = {"dateRange": date_range}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("google_ads_get_ad_performance", lambda: get_ad_performance(req, request_id=None))


@mcp.tool()
def google_ads_get_device_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
    campaign_name: Annotated[str | None, Field(description="Optional: filter to a specific campaign name")] = None,
) -> dict:
    """Get performance breakdown by device (MOBILE, DESKTOP, TABLET). Use to optimize device bid adjustments."""
    payload: dict = {"dateRange": date_range}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("google_ads_get_device_performance", lambda: get_device_performance(req, request_id=None))


@mcp.tool()
def google_ads_get_geo_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
    campaign_name: Annotated[str | None, Field(description="Optional: filter to a specific campaign name")] = None,
) -> dict:
    """Get performance by city and region. Shows where clicks and conversions are coming from geographically."""
    payload: dict = {"dateRange": date_range}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("google_ads_get_geo_performance", lambda: get_geo_performance(req, request_id=None))


@mcp.tool()
def google_ads_get_schedule_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
    campaign_name: Annotated[str | None, Field(description="Optional: filter to a specific campaign name")] = None,
) -> dict:
    """Get performance by hour of day and day of week. Use to identify peak times for ad scheduling optimization."""
    payload: dict = {"dateRange": date_range}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("google_ads_get_schedule_performance", lambda: get_schedule_performance(req, request_id=None))


@mcp.tool()
def google_ads_get_audience_performance(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_30_DAYS', 'LAST_7_DAYS', 'THIS_MONTH'")] = "LAST_30_DAYS",
    campaign_name: Annotated[str | None, Field(description="Optional: filter to a specific campaign name")] = None,
) -> dict:
    """Get performance by audience segment (remarketing lists, in-market audiences). Use to evaluate audience bid adjustments."""
    payload: dict = {"dateRange": date_range}
    if campaign_name:
        payload["campaignName"] = campaign_name
    req = ToolRequest(businessKey=business_key, payload=payload)
    return _run_tool("google_ads_get_audience_performance", lambda: get_audience_performance(req, request_id=None))


@mcp.tool()
def google_ads_get_conversion_actions(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
) -> dict:
    """List all conversion actions configured in the account (calls, form fills, etc.) with their status and counting type."""
    req = ToolRequest(businessKey=business_key)
    return _run_tool("google_ads_get_conversion_actions", lambda: get_conversion_actions(req, request_id=None))


@mcp.tool()
def google_ads_get_change_history(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range, e.g. 'LAST_7_DAYS', 'LAST_14_DAYS', 'LAST_30_DAYS'")] = "LAST_14_DAYS",
) -> dict:
    """Get the change history log — who changed what, when. Useful for auditing recent modifications to campaigns."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range})
    return _run_tool("google_ads_get_change_history", lambda: get_change_history(req, request_id=None))


@mcp.tool()
def google_ads_get_recommendations(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
) -> dict:
    """Get Google's optimization recommendations for the account (budget increases, bid adjustments, keyword suggestions, etc.)."""
    req = ToolRequest(businessKey=business_key)
    return _run_tool("google_ads_get_recommendations", lambda: get_recommendations(req, request_id=None))


@mcp.tool()
def google_ads_add_negative_keyword(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    campaign_name: Annotated[str, Field(description="Exact campaign name to add the negative keyword to")],
    keyword_text: Annotated[str, Field(description="The keyword text to block, e.g. 'free electrician'")],
    match_type: Annotated[str, Field(description="Match type: 'BROAD', 'PHRASE', or 'EXACT'")] = "EXACT",
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Add a negative keyword to a campaign to block irrelevant searches.

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(
        businessKey=business_key,
        dryRun=dry_run,
        approvalId=approval_id,
        payload={"campaignName": campaign_name, "keywordText": keyword_text, "matchType": match_type},
    )
    return _run_tool("google_ads_add_negative_keyword", lambda: add_negative_keyword(req, request_id=None))


@mcp.tool()
def google_ads_update_keyword_bid(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    keyword_text: Annotated[str, Field(description="Exact keyword text to update the bid for")],
    new_cpc_bid: Annotated[float, Field(description="New max CPC bid in USD, e.g. 5.50")],
    campaign_name: Annotated[str | None, Field(description="Optional: campaign name to disambiguate the keyword")] = None,
    ad_group_name: Annotated[str | None, Field(description="Optional: ad group name to disambiguate the keyword")] = None,
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Update the max CPC bid for a specific keyword.

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    payload: dict = {"keywordText": keyword_text, "newCpcBid": new_cpc_bid}
    if campaign_name:
        payload["campaignName"] = campaign_name
    if ad_group_name:
        payload["adGroupName"] = ad_group_name
    req = ToolRequest(businessKey=business_key, dryRun=dry_run, approvalId=approval_id, payload=payload)
    return _run_tool("google_ads_update_keyword_bid", lambda: update_keyword_bid(req, request_id=None))


@mcp.tool()
def google_ads_update_ad_status(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    ad_id: Annotated[str, Field(description="Numeric Google Ads ad ID (from get_ad_performance)")],
    status: Annotated[str, Field(description="New status: 'ENABLED' or 'PAUSED'")],
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Pause or enable a specific ad by ID.

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(
        businessKey=business_key,
        dryRun=dry_run,
        approvalId=approval_id,
        payload={"adId": ad_id, "status": status},
    )
    return _run_tool("google_ads_update_ad_status", lambda: update_ad_status(req, request_id=None))


@mcp.tool()
def google_ads_create_rsa(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    campaign_name: Annotated[str, Field(description="Exact campaign name for the new ad")],
    ad_group_name: Annotated[str, Field(description="Exact ad group name for the new ad")],
    final_url: Annotated[str, Field(description="Landing page URL for the ad, e.g. 'https://example.com/electrician'")],
    headlines: Annotated[list[str], Field(description="3–15 headline strings (max 30 chars each)")],
    descriptions: Annotated[list[str], Field(description="2–4 description strings (max 90 chars each)")],
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Create a new Responsive Search Ad (RSA) in an existing ad group.

    IMPORTANT: Always call with dry_run=true first. Show the user the full ad
    copy and only call with dry_run=false after they explicitly approve.
    Google requires 3–15 headlines and 2–4 descriptions.
    """
    req = ToolRequest(
        businessKey=business_key,
        dryRun=dry_run,
        approvalId=approval_id,
        payload={
            "campaignName": campaign_name,
            "adGroupName": ad_group_name,
            "headlines": headlines,
            "descriptions": descriptions,
            "finalUrl": final_url,
        },
    )
    return _run_tool("google_ads_create_rsa", lambda: create_rsa(req, request_id=None))


@mcp.tool()
def google_ads_update_campaign_bidding_strategy(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    campaign_name: Annotated[str, Field(description="Exact campaign name as it appears in Google Ads")],
    bidding_strategy: Annotated[str, Field(description="Strategy: MAXIMIZE_CLICKS, MAXIMIZE_CONVERSIONS, TARGET_CPA, TARGET_ROAS, or MANUAL_CPC")],
    target_cpa_micros: Annotated[int | None, Field(description="Target CPA in micros (e.g. 50000000 = $50). Required for TARGET_CPA.")] = None,
    target_roas: Annotated[float | None, Field(description="Target ROAS as a multiplier (e.g. 4.0 = 400%). Required for TARGET_ROAS.")] = None,
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Switch a campaign's bidding strategy (e.g. from MANUAL_CPC to MAXIMIZE_CLICKS).

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    Changing bidding strategy will reset the learning period.
    """
    payload: dict = {"campaignName": campaign_name, "biddingStrategy": bidding_strategy}
    if target_cpa_micros:
        payload["targetCpaMicros"] = target_cpa_micros
    if target_roas:
        payload["targetRoas"] = target_roas
    req = ToolRequest(businessKey=business_key, dryRun=dry_run, approvalId=approval_id, payload=payload)
    return _run_tool(
        "google_ads_update_campaign_bidding_strategy",
        lambda: update_campaign_bidding_strategy(req, request_id=None),
    )


if __name__ == "__main__":
    mcp.run()
