from __future__ import annotations

from typing import Any, Callable

from shared.meta_ads_client import (
    get_ad_insights_snapshot,
    get_adset_insights_snapshot,
    get_audience_breakdown,
    get_campaign_insights_snapshot,
    get_publisher_platform_breakdown as fetch_publisher_platform_breakdown,
    list_ad_accounts as fetch_ad_accounts,
    list_ad_creatives,
)
from shared.models import ToolRequest
from shared.responses import build_success_response
from shared.runtime_config import load_meta_ads_config

SERVICE_NAME = "meta-ads"
DATE_PRESETS = {
    "LAST_7_DAYS": "last_7d",
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