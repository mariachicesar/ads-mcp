from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from shared.meta_ads_client import (
    get_ad_insights_snapshot,
    get_adset_insights_snapshot,
    get_audience_breakdown,
    get_campaign_insights_snapshot,
    get_instagram_media_insights,
    get_instagram_online_followers,
    get_instagram_user_id,
    get_page_post_insights,
    get_publisher_platform_breakdown as fetch_publisher_platform_breakdown,
    list_ad_accounts as fetch_ad_accounts,
    list_ad_creatives,
)
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response
from shared.runtime_config import load_meta_ads_config
from shared.scheduling import SCHEDULE_TZ

SERVICE_NAME = "meta-ads"
DATE_PRESETS = {
    "LAST_7_DAYS": "last_7d",
    "LAST_14_DAYS": "last_14d",
    "LAST_30_DAYS": "last_30d",
    "THIS_MONTH": "this_month",
    "LAST_MONTH": "last_month",
}
LEVEL_FETCHERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "campaign": get_campaign_insights_snapshot,
    "adset": get_adset_insights_snapshot,
    "ad": get_ad_insights_snapshot,
}


def _date_preset(payload: dict[str, Any]) -> tuple[str, str]:
    requested = payload.get("dateRange", "LAST_30_DAYS")
    normalized = requested.upper() if isinstance(requested, str) else "LAST_30_DAYS"
    if normalized not in DATE_PRESETS:
        normalized = "LAST_30_DAYS"
    return normalized, DATE_PRESETS[normalized]


def _ids(payload: dict[str, Any], key: str) -> list[str] | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value if str(item)] or None
    return [str(value)] if str(value) else None


def _number(value: Any, *, integer: bool = False) -> int | float:
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        return 0 if integer else 0.0


def _conversion_actions(row: dict[str, Any]) -> list[dict[str, Any]]:
    costs_by_type = {
        str(item.get("action_type")): _number(item.get("value"))
        for item in row.get("cost_per_action_type", [])
        if isinstance(item, dict) and item.get("action_type")
    }
    return [
        {
            "actionType": str(item["action_type"]),
            "value": _number(item.get("value")),
            "costPerAction": costs_by_type.get(str(item["action_type"])),
        }
        for item in row.get("actions", [])
        if isinstance(item, dict) and item.get("action_type")
    ]


def _normalize_insight(row: dict[str, Any], level: str) -> dict[str, Any]:
    impressions = _number(row.get("impressions"), integer=True)
    clicks = _number(row.get("clicks"), integer=True)
    spend = _number(row.get("spend"))
    name_key, id_key = f"{level}_name", f"{level}_id"
    result = {
        "id": str(row.get(id_key, "")), "name": row.get(name_key),
        "status": row.get("status"), "impressions": impressions, "clicks": clicks,
        "spend": spend, "ctr": _number(row.get("ctr")), "cpc": _number(row.get("cpc")),
        "reach": _number(row.get("reach"), integer=True), "frequency": _number(row.get("frequency")),
        "conversionActions": _conversion_actions(row),
    }
    for dimension in ("publisher_platform", "age", "gender", "country"):
        if dimension in row:
            result[dimension] = row[dimension]
    return result


def _summary(rows: list[dict[str, Any]], label: str, date_range: str) -> tuple[str, dict[str, Any]]:
    totals = {
        "impressions": sum(row["impressions"] for row in rows),
        "clicks": sum(row["clicks"] for row in rows),
        "spend": round(sum(row["spend"] for row in rows), 2),
    }
    text = f"{len(rows)} {label}: {totals['impressions']:,} impressions, {totals['clicks']:,} clicks, ${totals['spend']:.2f} spend ({date_range})." if rows else f"No {label} data for {date_range}."
    return text, totals


def list_ad_accounts(request: ToolRequest, request_id: str | None) -> dict:
    config = load_meta_ads_config(business_key=request.businessKey, tool="list_ad_accounts")
    accounts = fetch_ad_accounts(config, tool="list_ad_accounts")
    return build_success_response(
        service=SERVICE_NAME, tool="list_ad_accounts", mode="read", business_key=request.businessKey,
        request_id=request_id, summary="Retrieved configured Meta Ads account.",
        data={"accounts": accounts}, freshness={"state": "live"},
    )


def _performance(request: ToolRequest, request_id: str | None, *, level: str) -> dict:
    tool = f"get_{level}_performance"
    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    payload = request.payload or {}
    date_range, date_preset = _date_preset(payload)
    ids = _ids(payload, f"{level}Ids")
    snapshot = LEVEL_FETCHERS[level](config, **{f"{level}_ids": ids}, date_preset=date_preset, tool=tool)
    rows = [_normalize_insight(row, level) for row in snapshot]
    summary, totals = _summary(rows, f"{level}s", date_range)
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey,
        request_id=request_id, summary=summary,
        data={"dateRange": date_range, "datePreset": date_preset, "rows": rows, "summary": totals},
        freshness={"state": "live"},
    )


def get_campaign_performance(request: ToolRequest, request_id: str | None) -> dict:
    return _performance(request, request_id, level="campaign")


def get_adset_performance(request: ToolRequest, request_id: str | None) -> dict:
    return _performance(request, request_id, level="adset")


def get_ad_performance(request: ToolRequest, request_id: str | None) -> dict:
    return _performance(request, request_id, level="ad")


def _breakdown(request: ToolRequest, request_id: str | None, *, audience: bool) -> dict:
    tool = "get_audience_performance" if audience else "get_publisher_platform_breakdown"
    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    payload = request.payload or {}
    level = payload.get("level", "campaign")
    if level not in LEVEL_FETCHERS:
        level = "campaign"
    date_range, date_preset = _date_preset(payload)
    object_ids = _ids(payload, f"{level}Ids")
    snapshot = (get_audience_breakdown(config, level=level, object_ids=object_ids, date_preset=date_preset, include_country=bool(payload.get("includeCountry")), tool=tool) if audience else fetch_publisher_platform_breakdown(config, level=level, object_ids=object_ids, date_preset=date_preset, tool=tool))
    rows = [_normalize_insight(row, level) for row in snapshot]
    summary, totals = _summary(rows, "audience segments" if audience else "publisher platform rows", date_range)
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey,
        request_id=request_id, summary=summary,
        data={"level": level, "dateRange": date_range, "datePreset": date_preset, "rows": rows, "summary": totals},
        freshness={"state": "live"},
    )


def get_publisher_platform_breakdown(request: ToolRequest, request_id: str | None) -> dict:
    return _breakdown(request, request_id, audience=False)


def get_audience_performance(request: ToolRequest, request_id: str | None) -> dict:
    return _breakdown(request, request_id, audience=True)


def list_creative_assets(request: ToolRequest, request_id: str | None) -> dict:
    config = load_meta_ads_config(business_key=request.businessKey, tool="list_creative_assets")
    assets = list_ad_creatives(config, tool="list_creative_assets")
    return build_success_response(
        service=SERVICE_NAME, tool="list_creative_assets", mode="read", business_key=request.businessKey,
        request_id=request_id, summary=f"Retrieved {len(assets)} Meta Ads creative assets.",
        data={"assets": assets}, freshness={"state": "live"},
    )


# ── Organic insights ─────────────────────────────────────────────────────────

DEFAULT_POST_HOUR = 19
WINDOW_HOURS = (18, 19, 20)
MIN_PEAK_LIFT = 0.10


def _pacific(value: Any) -> str | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(SCHEDULE_TZ).isoformat()


def _hook(text: Any) -> str:
    lines = str(text or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(_number(value, integer=True))


def _instagram_row(item: dict[str, Any]) -> dict[str, Any]:
    metrics = item.get("insights") or {}
    row = {
        "id": str(item.get("id", "")), "hook": _hook(item.get("caption")),
        "mediaType": item.get("media_product_type"), "permalink": item.get("permalink"),
        "publishedPacific": _pacific(item.get("timestamp")),
        "likes": _number(item.get("like_count"), integer=True),
        "comments": _number(item.get("comments_count"), integer=True),
    }
    for key, metric in (("reach", "reach"), ("views", "views"), ("saved", "saved"),
                        ("shares", "shares"), ("totalInteractions", "total_interactions")):
        row[key] = _optional_int(metrics.get(metric))
    return row


def _facebook_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")), "hook": _hook(item.get("message")),
        "permalink": item.get("permalink_url"), "publishedPacific": _pacific(item.get("created_time")),
        "reach": _optional_int(item.get("reach")),
        "reactions": _number(item.get("reactions"), integer=True),
        "comments": _number(item.get("comments"), integer=True),
        "shares": _number(item.get("shares"), integer=True),
    }


def _to_pacific_hours(utc_hours: dict[int, int]) -> dict[int, int]:
    offset_hours = int((datetime.now(SCHEDULE_TZ).utcoffset() or timedelta(0)).total_seconds() // 3600)
    return {(int(hour) + offset_hours) % 24: int(count) for hour, count in utc_hours.items()}


def _recommend(by_hour: dict[int, int] | None) -> tuple[str, str]:
    default = f"{DEFAULT_POST_HOUR:02d}:00"
    if not by_hour:
        return default, f"No audience-by-hour data available; defaulting to {default} Pacific."
    counts = {hour: by_hour.get(hour) for hour in WINDOW_HOURS}
    if any(value is None for value in counts.values()):
        return default, f"Audience data does not cover hours 18-20 Pacific; defaulting to {default}."
    best = max(WINDOW_HOURS, key=lambda hour: (counts[hour], hour == DEFAULT_POST_HOUR))
    baseline = counts[DEFAULT_POST_HOUR]
    if best != DEFAULT_POST_HOUR and counts[best] > 0 and counts[best] >= baseline * (1 + MIN_PEAK_LIFT):
        return f"{best:02d}:00", (f"{best:02d}:00 Pacific has {counts[best]} followers online, "
                                  f"at least 10% above {default} ({baseline}).")
    return default, f"No hour in 18-20 Pacific beats {default} by 10%; keeping {default}."


def get_organic_insights(request: ToolRequest, request_id: str | None) -> dict:
    tool = "get_organic_insights"
    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    try:
        days = int((request.payload or {}).get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(90, days))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    page_id = str(config.get("page_id") or "")
    warnings: list[str] = []
    ig_posts: list[dict[str, Any]] = []
    fb_posts: list[dict[str, Any]] = []
    by_hour: dict[int, int] | None = None

    ig_user_id = None
    try:
        ig_user_id = get_instagram_user_id(config, page_id=page_id, tool=tool)
        if not ig_user_id:
            warnings.append("No Instagram professional account is linked to the configured Page.")
    except AdsMcpError as exc:
        warnings.append(f"Instagram account lookup failed: {exc.message}")
    if ig_user_id:
        try:
            raw = get_instagram_media_insights(config, ig_user_id=ig_user_id, since=since, tool=tool)
            ig_posts = [_instagram_row(item) for item in raw]
            errors = [item["insightsError"] for item in raw if item.get("insightsError")]
            if errors:
                warnings.append(f"Some Instagram media rejected insight metrics; their values are null ({errors[0]}).")
        except AdsMcpError as exc:
            warnings.append(f"Instagram media insights failed: {exc.message}")
        try:
            followers = get_instagram_online_followers(config, ig_user_id=ig_user_id, tool=tool)
        except AdsMcpError as exc:
            followers = None
            warnings.append(f"Instagram online_followers failed: {exc.message}")
        else:
            if not followers:
                warnings.append("Instagram online_followers is unavailable (needs 100+ followers and instagram_manage_insights).")
        if followers:
            by_hour = _to_pacific_hours(followers)
    if page_id:
        try:
            raw_fb = get_page_post_insights(config, page_id=page_id, since=since, tool=tool)
            fb_posts = [_facebook_row(item) for item in raw_fb]
            if any(item.get("insightsError") for item in raw_fb):
                warnings.append("Some Facebook posts rejected the reach metric; reach is null for them.")
        except AdsMcpError as exc:
            warnings.append(f"Facebook post insights failed: {exc.message}")
    else:
        warnings.append("No Facebook Page is configured; skipped Facebook insights.")

    time_of_day, reason = _recommend(by_hour)
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey,
        request_id=request_id,
        summary=f"{len(ig_posts)} Instagram and {len(fb_posts)} Facebook posts in the last {days} days; recommended time {time_of_day} Pacific.",
        data={
            "days": days,
            "instagram": {"posts": ig_posts, "audienceByHourPacific": dict(sorted(by_hour.items())) if by_hour else None},
            "facebook": {"posts": fb_posts},
            "recommendedWeekdayTime": time_of_day, "recommendationReason": reason, "warnings": warnings,
        },
        freshness={"state": "live"},
    )
