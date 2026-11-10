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
from tools.write import create_ad, create_adset, create_campaign, update_adset_schedule, update_budget, update_status
from tools.publish import list_scheduled_page_videos, schedule_page_reel, schedule_page_video, upload_ad_video

mcp = FastMCP(
    name="meta-ads",
    instructions=(
        "Tools for viewing Meta Ads Facebook and Instagram campaign performance. "
        "Instagram performance is returned through publisher platform breakdowns. "
        "Write tools: call first WITHOUT approval_id to get a dry-run preview and an approvalId, "
        "show the preview to the user, and only call again with that approval_id and the exact same "
        "arguments after explicit confirmation. approvalIds expire after 15 minutes. "
        "New campaigns, ad sets and ads are created PAUSED by default."
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
def meta_ads_get_campaign_performance(business_key: Annotated[str, Field(description="Business key")], date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS", campaign_ids: Annotated[list[str] | None, Field(description="Optional campaign IDs")] = None) -> dict:
    """Get campaign performance metrics from Meta Ads."""
    return _run_tool("get_campaign_performance", lambda: get_campaign_performance(_performance_request(business_key, date_range, campaign_ids, "campaignIds"), None))


@mcp.tool()
def meta_ads_get_adset_performance(business_key: Annotated[str, Field(description="Business key")], date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS", adset_ids: Annotated[list[str] | None, Field(description="Optional ad set IDs")] = None) -> dict:
    """Get ad set performance metrics from Meta Ads."""
    return _run_tool("get_adset_performance", lambda: get_adset_performance(_performance_request(business_key, date_range, adset_ids, "adsetIds"), None))


@mcp.tool()
def meta_ads_get_ad_performance(business_key: Annotated[str, Field(description="Business key")], date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS", ad_ids: Annotated[list[str] | None, Field(description="Optional ad IDs")] = None) -> dict:
    """Get ad performance metrics from Meta Ads."""
    return _run_tool("get_ad_performance", lambda: get_ad_performance(_performance_request(business_key, date_range, ad_ids, "adIds"), None))


@mcp.tool()
def meta_ads_get_publisher_platform_breakdown(business_key: Annotated[str, Field(description="Business key")], level: Annotated[str, Field(description="campaign, adset, or ad")] = "campaign", date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS") -> dict:
    """Compare Facebook, Instagram, Messenger, and Audience Network placement performance."""
    request = ToolRequest(businessKey=business_key, payload={"level": level, "dateRange": date_range})
    return _run_tool("get_publisher_platform_breakdown", lambda: get_publisher_platform_breakdown(request, None))


@mcp.tool()
def meta_ads_get_audience_performance(business_key: Annotated[str, Field(description="Business key")], level: Annotated[str, Field(description="campaign, adset, or ad")] = "campaign", date_range: Annotated[str, Field(description="LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, THIS_MONTH, or LAST_MONTH")] = "LAST_30_DAYS", include_country: Annotated[bool, Field(description="Include country in the audience breakdown")] = False) -> dict:
    """Get age and gender, optionally country, performance breakdowns."""
    request = ToolRequest(businessKey=business_key, payload={"level": level, "dateRange": date_range, "includeCountry": include_country})
    return _run_tool("get_audience_performance", lambda: get_audience_performance(request, None))


@mcp.tool()
def meta_ads_list_creative_assets(business_key: Annotated[str, Field(description="Business key")]) -> dict:
    """List creative assets already available in the Meta Ads account."""
    return _run_tool("list_creative_assets", lambda: list_creative_assets(ToolRequest(businessKey=business_key), None))


_BK = Annotated[str, Field(description="Business key, e.g. 'el-cuis'")]
_APPROVAL = Annotated[str | None, Field(description="Omit for a dry run. To execute, pass the approvalId from the dry-run response with identical arguments (expires after 15 minutes).")]
_CREATE_STATUS = Annotated[str, Field(description="PAUSED (default) or ACTIVE")]


def _write_request(business_key: str, approval_id: str | None, payload: dict) -> ToolRequest:
    return ToolRequest(
        businessKey=business_key, approvalId=approval_id, dryRun=approval_id is None,
        payload={k: v for k, v in payload.items() if v is not None},
    )


@mcp.tool()
def meta_ads_update_status(
    business_key: _BK,
    object_id: Annotated[str, Field(description="Campaign, ad set or ad ID")],
    status: Annotated[str, Field(description="ACTIVE, PAUSED or ARCHIVED")],
    approval_id: _APPROVAL = None,
) -> dict:
    """Change the status of a Meta campaign, ad set or ad.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {"objectId": object_id, "status": status})
    return _run_tool("update_status", lambda: update_status(request, None))


@mcp.tool()
def meta_ads_update_adset_schedule(
    business_key: _BK,
    adset_id: Annotated[str, Field(description="Ad set ID to schedule")],
    clone_schedule_from_adset_id: Annotated[str | None, Field(description="Copy the adset_schedule from this existing ad set")] = None,
    schedule: Annotated[list[dict] | None, Field(description="Blocks of {days: [0-6, 0=Sunday], start_minute, end_minute (whole hours, minutes from midnight), timezone_type: USER|ADVERTISER}")] = None,
    approval_id: _APPROVAL = None,
) -> dict:
    """Set when a Meta ad set delivers (dayparting / ad schedule).

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "adsetId": adset_id, "cloneScheduleFromAdsetId": clone_schedule_from_adset_id, "schedule": schedule,
    })
    return _run_tool("update_adset_schedule", lambda: update_adset_schedule(request, None))


_FILE_PATH = Annotated[str | None, Field(description="Local .mp4/.mov file readable by this server (max 1 GiB); use file_path OR file_url")]
_FILE_URL = Annotated[str | None, Field(description="Public https:// URL Meta fetches the video from; use file_path OR file_url")]
_WHEN = Annotated[str, Field(description="ISO 8601 publish time; naive = America/Los_Angeles. 10 minutes to 75 days from now")]
_PAGE = Annotated[str | None, Field(description="Defaults to the configured Page; other Pages are blocked")]


@mcp.tool()
def meta_ads_upload_ad_video(
    business_key: _BK,
    name: Annotated[str, Field(description="Video name in the ad Media library")],
    file_path: _FILE_PATH = None,
    file_url: _FILE_URL = None,
    approval_id: _APPROVAL = None,
) -> dict:
    """Upload a video to the ad account's Media library (advideos); returns its video ID.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {"name": name, "filePath": file_path, "fileUrl": file_url})
    return _run_tool("upload_ad_video", lambda: upload_ad_video(request, None))


@mcp.tool()
def meta_ads_schedule_page_video(
    business_key: _BK,
    scheduled_time: _WHEN,
    description: Annotated[str, Field(description="Post text")],
    file_path: _FILE_PATH = None,
    file_url: _FILE_URL = None,
    title: Annotated[str | None, Field(description="Optional video title")] = None,
    page_id: _PAGE = None,
    approval_id: _APPROVAL = None,
) -> dict:
    """Schedule a Facebook Page video post; Facebook publishes it at scheduled_time.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "scheduledTime": scheduled_time, "description": description, "filePath": file_path,
        "fileUrl": file_url, "title": title, "pageId": page_id,
    })
    return _run_tool("schedule_page_video", lambda: schedule_page_video(request, None))


@mcp.tool()
def meta_ads_schedule_page_reel(
    business_key: _BK,
    scheduled_time: _WHEN,
    description: Annotated[str, Field(description="Reel caption")],
    file_path: _FILE_PATH = None,
    file_url: _FILE_URL = None,
    page_id: _PAGE = None,
    approval_id: _APPROVAL = None,
) -> dict:
    """Schedule a Facebook Page Reel; Facebook publishes it at scheduled_time.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "scheduledTime": scheduled_time, "description": description, "filePath": file_path,
        "fileUrl": file_url, "pageId": page_id,
    })
    return _run_tool("schedule_page_reel", lambda: schedule_page_reel(request, None))


@mcp.tool()
def meta_ads_list_scheduled_page_videos(business_key: _BK, page_id: _PAGE = None) -> dict:
    """List videos and Reels scheduled (not yet published) on the Facebook Page."""
    request = ToolRequest(businessKey=business_key, payload={"pageId": page_id} if page_id else {})
    return _run_tool("list_scheduled_page_videos", lambda: list_scheduled_page_videos(request, None))


@mcp.tool()
def meta_ads_update_budget(
    business_key: _BK,
    object_id: Annotated[str, Field(description="Campaign (CBO) or ad set ID")],
    daily_budget_usd: Annotated[float | None, Field(description="Daily budget in USD, e.g. 15 (sent to Meta in cents)")] = None,
    lifetime_budget_usd: Annotated[float | None, Field(description="Lifetime budget in USD (sent to Meta in cents)")] = None,
    approval_id: _APPROVAL = None,
) -> dict:
    """Set the daily or lifetime budget (USD) on a Meta campaign or ad set.

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "objectId": object_id, "dailyBudgetUsd": daily_budget_usd, "lifetimeBudgetUsd": lifetime_budget_usd,
    })
    return _run_tool("update_budget", lambda: update_budget(request, None))


@mcp.tool()
def meta_ads_create_campaign(
    business_key: _BK,
    name: Annotated[str, Field(description="Campaign name")],
    objective: Annotated[str, Field(description="OUTCOME_ENGAGEMENT, OUTCOME_LEADS, OUTCOME_TRAFFIC, OUTCOME_SALES, OUTCOME_AWARENESS or OUTCOME_APP_PROMOTION")],
    daily_budget_usd: Annotated[float | None, Field(description="Optional campaign (CBO) daily budget in USD; omit to use ad set budgets")] = None,
    lifetime_budget_usd: Annotated[float | None, Field(description="Optional campaign (CBO) lifetime budget in USD")] = None,
    special_ad_categories: Annotated[list[str] | None, Field(description="Usually empty. HOUSING, EMPLOYMENT, CREDIT, ISSUES_ELECTIONS_POLITICS or FINANCIAL_PRODUCTS_SERVICES")] = None,
    bid_strategy: Annotated[str | None, Field(description="CBO only; defaults to LOWEST_COST_WITHOUT_CAP")] = None,
    status: _CREATE_STATUS = "PAUSED",
    approval_id: _APPROVAL = None,
) -> dict:
    """Create a Meta campaign (PAUSED by default).

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "name": name, "objective": objective, "dailyBudgetUsd": daily_budget_usd,
        "lifetimeBudgetUsd": lifetime_budget_usd, "specialAdCategories": special_ad_categories or [],
        "bidStrategy": bid_strategy, "status": status,
    })
    return _run_tool("create_campaign", lambda: create_campaign(request, None))


@mcp.tool()
def meta_ads_create_adset(
    business_key: _BK,
    campaign_id: Annotated[str, Field(description="Parent campaign ID")],
    name: Annotated[str, Field(description="Ad set name")],
    optimization_goal: Annotated[str, Field(description="e.g. CONVERSATIONS, LINK_CLICKS, LEAD_GENERATION, REACH")],
    latitude: Annotated[float | None, Field(description="Center latitude for radius targeting; required unless clone_targeting_from_adset_id is set")] = None,
    longitude: Annotated[float | None, Field(description="Center longitude for radius targeting; required unless clone_targeting_from_adset_id is set")] = None,
    radius_miles: Annotated[float | None, Field(description="Radius in miles (1-50); required unless clone_targeting_from_adset_id is set")] = None,
    clone_targeting_from_adset_id: Annotated[str | None, Field(description="Copy the full targeting (geo, ages, locales, placements, Advantage+ settings) verbatim from this existing ad set; geo/age/advantage_audience arguments are then ignored")] = None,
    daily_budget_usd: Annotated[float | None, Field(description="Ad set daily budget in USD; omit when the campaign uses CBO")] = None,
    lifetime_budget_usd: Annotated[float | None, Field(description="Ad set lifetime budget in USD; requires end_time")] = None,
    destination_type: Annotated[str | None, Field(description="e.g. MESSENGER, WHATSAPP, INSTAGRAM_DIRECT, WEBSITE")] = None,
    age_min: Annotated[int, Field(description="18-65")] = 18,
    age_max: Annotated[int, Field(description="18-65")] = 65,
    page_id: Annotated[str | None, Field(description="promoted_object page; defaults to the configured page")] = None,
    billing_event: Annotated[str, Field(description="Defaults to IMPRESSIONS")] = "IMPRESSIONS",
    bid_strategy: Annotated[str | None, Field(description="Ad set budget only; defaults to LOWEST_COST_WITHOUT_CAP")] = None,
    start_time: Annotated[str | None, Field(description="ISO 8601 start time")] = None,
    end_time: Annotated[str | None, Field(description="ISO 8601 end time")] = None,
    advantage_audience: Annotated[int, Field(description="targeting_automation.advantage_audience; 0 keeps targeting strict")] = 0,
    status: _CREATE_STATUS = "PAUSED",
    approval_id: _APPROVAL = None,
) -> dict:
    """Create a Meta ad set with radius + age targeting, or targeting cloned from an existing ad set (PAUSED by default).

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "campaignId": campaign_id, "name": name, "optimizationGoal": optimization_goal,
        "latitude": latitude, "longitude": longitude, "radiusMiles": radius_miles,
        "dailyBudgetUsd": daily_budget_usd, "lifetimeBudgetUsd": lifetime_budget_usd,
        "destinationType": destination_type, "ageMin": age_min, "ageMax": age_max,
        "pageId": page_id, "billingEvent": billing_event, "bidStrategy": bid_strategy,
        "startTime": start_time, "endTime": end_time, "advantageAudience": advantage_audience,
        "cloneTargetingFromAdsetId": clone_targeting_from_adset_id, "status": status,
    })
    return _run_tool("create_adset", lambda: create_adset(request, None))


@mcp.tool()
def meta_ads_create_ad(
    business_key: _BK,
    adset_id: Annotated[str, Field(description="Parent ad set ID")],
    name: Annotated[str, Field(description="Ad name")],
    object_story_id: Annotated[str | None, Field(description="Existing page post '<pageId>_<postId>'; if set, the creative fields below are ignored")] = None,
    message: Annotated[str | None, Field(description="Primary text")] = None,
    image_hash: Annotated[str | None, Field(description="Image hash from the ad account image library")] = None,
    image_url: Annotated[str | None, Field(description="Public image URL (used if no image_hash)")] = None,
    video_id: Annotated[str | None, Field(description="Ad video ID; needs image_hash or image_url as the thumbnail")] = None,
    headline: Annotated[str | None, Field(description="Headline")] = None,
    call_to_action_type: Annotated[str, Field(description="Defaults to MESSAGE_PAGE (click-to-message)")] = "MESSAGE_PAGE",
    link: Annotated[str | None, Field(description="Destination URL; not needed for MESSAGE_PAGE")] = None,
    app_destination: Annotated[str | None, Field(description="MESSAGE_PAGE only: MESSENGER (default), WHATSAPP or INSTAGRAM_DIRECT")] = None,
    page_id: Annotated[str | None, Field(description="Defaults to the configured page")] = None,
    status: _CREATE_STATUS = "PAUSED",
    approval_id: _APPROVAL = None,
) -> dict:
    """Create an ad creative and then an ad that uses it (PAUSED by default).

    IMPORTANT: Call without approval_id first and show the dry-run preview.
    Only call again with approval_id after the user explicitly approves.
    """
    request = _write_request(business_key, approval_id, {
        "adsetId": adset_id, "name": name, "objectStoryId": object_story_id, "message": message,
        "imageHash": image_hash, "imageUrl": image_url, "videoId": video_id, "headline": headline,
        "callToActionType": call_to_action_type, "link": link, "appDestination": app_destination,
        "pageId": page_id, "status": status,
    })
    return _run_tool("create_ad", lambda: create_ad(request, None))


if __name__ == "__main__":
    mcp.run()
