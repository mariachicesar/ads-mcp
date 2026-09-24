from __future__ import annotations

from importlib import import_module
from typing import Any

from shared.errors import AdsMcpError


INSIGHT_FIELDS = [
    "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
    "impressions", "clicks", "spend", "ctr", "cpc", "reach", "frequency",
    "actions", "cost_per_action_type",
]


def _account_id(value: str | None, *, tool: str | None) -> str:
    if not value:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Meta Ads account ID is not configured.",
            tool=tool,
        )
    value = value.strip()
    return value if value.startswith("act_") else f"act_{value}"


def build_meta_ads_api(config: dict[str, Any], *, tool: str | None = None):
    try:
        api_module = import_module("facebook_business.api")
    except ModuleNotFoundError as exc:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="facebook-business SDK is not installed in the current environment.",
            tool=tool,
        ) from exc

    access_token = config.get("access_token")
    if not access_token:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Meta Ads access token is not configured.",
            tool=tool,
        )

    try:
        return api_module.FacebookAdsApi.init(
            app_id=config.get("app_id"),
            app_secret=config.get("app_secret"),
            access_token=access_token,
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Meta Ads client configuration could not be initialized.",
            tool=tool,
            details={"reason": str(exc)},
        ) from exc


def get_ad_account(config: dict[str, Any], *, tool: str | None = None):
    try:
        ad_account_module = import_module("facebook_business.adobjects.adaccount")
        api = build_meta_ads_api(config, tool=tool)
        return ad_account_module.AdAccount(_account_id(config.get("ad_account_id"), tool=tool), api=api)
    except AdsMcpError:
        raise
    except Exception as exc:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Meta Ads account client could not be initialized.",
            tool=tool,
            details={"reason": str(exc)},
        ) from exc


def list_ad_accounts(config: dict[str, Any], *, tool: str | None = None) -> list[dict[str, Any]]:
    account = get_ad_account(config, tool=tool)
    try:
        result = account.api_get(fields=["id", "name", "account_status", "currency", "timezone_name"])
        row = dict(result)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Meta Ads account lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc
    return [{
        "adAccountId": str(row.get("id") or _account_id(config.get("ad_account_id"), tool=tool)),
        "name": row.get("name"),
        "status": row.get("account_status"),
        "currency": row.get("currency"),
        "timezoneName": row.get("timezone_name"),
        "isConfiguredAccount": True,
    }]


def _get_insights(
    config: dict[str, Any], *, level: str, date_preset: str,
    breakdowns: list[str] | None = None, entity_ids: list[str] | None = None,
    tool: str | None = None,
) -> list[dict[str, Any]]:
    account = get_ad_account(config, tool=tool)
    params: dict[str, Any] = {"level": level, "date_preset": date_preset}
    if breakdowns:
        params["breakdowns"] = breakdowns
    if entity_ids:
        params["filtering"] = [{
            "field": f"{level}.id", "operator": "IN", "value": entity_ids,
        }]
    try:
        return [dict(row) for row in account.get_insights(fields=INSIGHT_FIELDS, params=params)]
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message=f"Meta Ads {level} insights query failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc


def get_campaign_insights_snapshot(config: dict[str, Any], *, campaign_ids: list[str] | None = None, date_preset: str = "last_30d", breakdowns: list[str] | None = None, tool: str | None = None) -> list[dict[str, Any]]:
    return _get_insights(config, level="campaign", date_preset=date_preset, breakdowns=breakdowns, entity_ids=campaign_ids, tool=tool)


def get_adset_insights_snapshot(config: dict[str, Any], *, adset_ids: list[str] | None = None, date_preset: str = "last_30d", breakdowns: list[str] | None = None, tool: str | None = None) -> list[dict[str, Any]]:
    return _get_insights(config, level="adset", date_preset=date_preset, breakdowns=breakdowns, entity_ids=adset_ids, tool=tool)


def get_ad_insights_snapshot(config: dict[str, Any], *, ad_ids: list[str] | None = None, date_preset: str = "last_30d", breakdowns: list[str] | None = None, tool: str | None = None) -> list[dict[str, Any]]:
    return _get_insights(config, level="ad", date_preset=date_preset, breakdowns=breakdowns, entity_ids=ad_ids, tool=tool)


def get_publisher_platform_breakdown(config: dict[str, Any], *, level: str, object_ids: list[str] | None = None, date_preset: str = "last_30d", tool: str | None = None) -> list[dict[str, Any]]:
    return _get_insights(config, level=level, date_preset=date_preset, breakdowns=["publisher_platform"], entity_ids=object_ids, tool=tool)


def get_audience_breakdown(config: dict[str, Any], *, level: str, object_ids: list[str] | None = None, date_preset: str = "last_30d", include_country: bool = False, tool: str | None = None) -> list[dict[str, Any]]:
    breakdowns = ["age", "gender"]
    if include_country:
        breakdowns.append("country")
    return _get_insights(config, level=level, date_preset=date_preset, breakdowns=breakdowns, entity_ids=object_ids, tool=tool)


def list_ad_creatives(config: dict[str, Any], *, tool: str | None = None) -> list[dict[str, Any]]:
    account = get_ad_account(config, tool=tool)
    try:
        return [dict(row) for row in account.get_ad_creatives(fields=["id", "name", "thumbnail_url", "status", "object_story_spec"])]
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Meta Ads creative asset lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc