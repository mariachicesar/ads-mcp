from __future__ import annotations

import hashlib
import hmac
import json
import os
from importlib import import_module
from typing import Any

from shared.errors import AdsMcpError


DEFAULT_API_VERSION = "v23.0"
GRAPH_BASE_URL = "https://graph.facebook.com"
GRAPH_VIDEO_URL = "https://graph-video.facebook.com"
RUPLOAD_URL = "https://rupload.facebook.com/video-upload"

INSIGHT_FIELDS = [
    "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
    "impressions", "clicks", "spend", "ctr", "cpc", "reach", "frequency",
    "actions", "cost_per_action_type",
]


def _graph_error_details(error: dict[str, Any]) -> dict[str, Any]:
    details = {
        "reason": error.get("message"),
        "type": error.get("type"),
        "code": error.get("code"),
        "subcode": error.get("error_subcode"),
        "fbtraceId": error.get("fbtrace_id"),
        "userTitle": error.get("error_user_title"),
        "userMessage": error.get("error_user_msg"),
    }
    return {key: value for key, value in details.items() if value is not None}


def _upstream_details(exc: Exception) -> dict[str, Any]:
    """Pull Graph code/subcode/fbtrace out of a facebook_business FacebookRequestError."""
    body = getattr(exc, "body", None)
    body = body() if callable(body) else body
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return _graph_error_details(body["error"])
    return {"reason": str(exc)}


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
            api_version=config.get("api_version") or DEFAULT_API_VERSION,
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
            details=_upstream_details(exc),
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
            details=_upstream_details(exc),
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
            details=_upstream_details(exc),
        ) from exc

# ── Graph API write layer ────────────────────────────────────────────────────
# Writes go through plain HTTPS so the token travels only in the
# Authorization header, never in the URL or form body.


def _graph_request(
    config: dict[str, Any], method: str, path: str, *,
    params: dict[str, Any] | None = None, data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None, base_url: str | None = None,
    access_token: str | None = None, tool: str | None = None,
) -> dict[str, Any]:
    access_token = access_token or config.get("access_token")
    if not access_token:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Meta Ads access token is not configured.",
            tool=tool,
        )
    requests = import_module("requests")
    query = dict(params or {})
    app_secret = config.get("app_secret")
    if app_secret:
        # appsecret_proof is an HMAC of the token, safe to send in the query string.
        query["appsecret_proof"] = hmac.new(
            app_secret.encode("utf-8"), access_token.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    form = None
    if data is not None:
        form = {
            key: json.dumps(value) if isinstance(value, (dict, list, bool)) else value
            for key, value in data.items() if value is not None
        }
    version = config.get("api_version") or DEFAULT_API_VERSION
    url = f"{base_url or GRAPH_BASE_URL}/{version}/{path.lstrip('/')}"
    extra = {"files": files} if files else {}
    try:
        response = requests.request(
            method, url, params=query, data=form,
            headers={"Authorization": f"Bearer {access_token}"}, timeout=300 if files else 30, **extra,
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message=f"Meta Graph API request failed: {method} {path}.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 or (isinstance(body, dict) and "error" in body):
        error = body.get("error") if isinstance(body, dict) else None
        details = _graph_error_details(error) if isinstance(error, dict) else {"reason": response.text[:500]}
        details["httpStatus"] = response.status_code
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message=f"Meta Graph API rejected {method} {path}.",
            tool=tool,
            retryable=bool(isinstance(error, dict) and error.get("is_transient")),
            details=details,
        )
    return body if isinstance(body, dict) else {"data": body}


# Each list carries a field only that type has (objective / optimization_goal /
# creative), so a "nonexisting field" error rules the type out.
OBJECT_SNAPSHOT_FIELDS = {
    "campaign": ["id", "name", "account_id", "status", "effective_status", "objective", "daily_budget", "lifetime_budget"],
    "adset": ["id", "name", "account_id", "campaign_id", "status", "effective_status", "optimization_goal", "daily_budget", "lifetime_budget", "end_time"],
    "ad": ["id", "name", "account_id", "campaign_id", "adset_id", "status", "effective_status", "creative"],
}


def _is_wrong_type_error(exc: AdsMcpError) -> bool:
    return exc.details.get("code") == 100 and "nonexisting field" in str(exc.details.get("reason", ""))


def get_object_snapshot(config: dict[str, Any], *, object_id: str, tool: str | None = None) -> dict[str, Any]:
    """Return a campaign/ad set/ad with its type, so callers can show before-values."""
    for object_type, fields in OBJECT_SNAPSHOT_FIELDS.items():
        try:
            row = _graph_request(config, "GET", object_id, params={"fields": ",".join(fields)}, tool=tool)
        except AdsMcpError as exc:
            if _is_wrong_type_error(exc):
                continue
            raise
        return {**row, "objectType": object_type}
    raise AdsMcpError(
        status_code=400,
        error_code="REQUEST_INVALID",
        message=f"Object {object_id} is not a campaign, ad set or ad.",
        tool=tool,
    )


def get_account_snapshot(config: dict[str, Any], *, tool: str | None = None) -> dict[str, Any]:
    account_id = _account_id(config.get("ad_account_id"), tool=tool)
    return _graph_request(config, "GET", account_id, params={"fields": "id,name,currency,account_status"}, tool=tool)


def get_adset_targeting(config: dict[str, Any], *, adset_id: str, tool: str | None = None) -> dict[str, Any]:
    """Return an ad set's id, name, account_id and full targeting spec."""
    return _graph_request(config, "GET", adset_id, params={"fields": "id,name,account_id,targeting"}, tool=tool)


def get_adset_schedule(config: dict[str, Any], *, adset_id: str, tool: str | None = None) -> dict[str, Any]:
    """Return an ad set's id, name, account_id, budgets, pacing_type and adset_schedule."""
    fields = "id,name,account_id,status,daily_budget,lifetime_budget,pacing_type,adset_schedule"
    return _graph_request(config, "GET", adset_id, params={"fields": fields}, tool=tool)


def get_page_access_token(config: dict[str, Any], *, page_id: str, tool: str | None = None) -> str:
    """Exchange the configured user token for the Page's access token (never stored)."""
    hint = (f"Could not get a Page access token for {page_id}; the Meta user token needs "
            "pages_manage_posts and pages_read_engagement, and the user must manage this Page.")
    try:
        row = _graph_request(config, "GET", page_id, params={"fields": "access_token"}, tool=tool)
    except AdsMcpError as exc:
        raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool=tool, message=hint,
                          retryable=exc.retryable, details=exc.details) from exc
    token = row.get("access_token")
    if not token:
        raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool=tool, message=hint)
    return str(token)


def post_video(
    config: dict[str, Any], *, path: str, fields: dict[str, Any],
    file_path: str | None, file_url: str | None,
    access_token: str | None = None, tool: str | None = None,
) -> dict[str, Any]:
    """POST a video to graph-video.facebook.com as multipart (local file) or by file_url."""
    if file_path:
        with open(file_path, "rb") as fh:
            return _graph_request(config, "POST", path, data=dict(fields), files={"source": fh},
                                  base_url=GRAPH_VIDEO_URL, access_token=access_token, tool=tool)
    return _graph_request(config, "POST", path, data={**fields, "file_url": file_url},
                          base_url=GRAPH_VIDEO_URL, access_token=access_token, tool=tool)


def upload_reel_bytes(
    config: dict[str, Any], *, video_id: str, access_token: str,
    file_path: str | None, file_url: str | None, tool: str | None = None,
) -> dict[str, Any]:
    """Send a Reel's bytes (or its hosted URL) to rupload.facebook.com for an upload session."""
    requests = import_module("requests")
    version = config.get("api_version") or DEFAULT_API_VERSION
    url = f"{RUPLOAD_URL}/{version}/{video_id}"
    headers = {"Authorization": f"OAuth {access_token}"}
    try:
        if file_path:
            headers.update(offset="0", file_size=str(os.path.getsize(file_path)))
            with open(file_path, "rb") as fh:
                response = requests.post(url, headers=headers, data=fh, timeout=300)
        else:
            headers["file_url"] = str(file_url)
            response = requests.post(url, headers=headers, timeout=300)
    except Exception as exc:
        raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool=tool, retryable=True,
                          message=f"Reel upload request failed for video {video_id}.",
                          details={"reason": str(exc), "videoId": video_id}) from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 or (isinstance(body, dict) and "error" in body):
        error = body.get("error") if isinstance(body, dict) else None
        details = _graph_error_details(error) if isinstance(error, dict) else {"reason": response.text[:500]}
        raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool=tool,
                          message=f"Meta rejected the Reel upload for video {video_id}.",
                          details={**details, "httpStatus": response.status_code, "videoId": video_id})
    return body if isinstance(body, dict) else {"data": body}


def update_object(config: dict[str, Any], *, object_id: str, fields: dict[str, Any], tool: str | None = None) -> dict[str, Any]:
    return _graph_request(config, "POST", object_id, data=fields, tool=tool)


def create_account_object(config: dict[str, Any], *, edge: str, fields: dict[str, Any], tool: str | None = None) -> dict[str, Any]:
    account_id = _account_id(config.get("ad_account_id"), tool=tool)
    return _graph_request(config, "POST", f"{account_id}/{edge}", data=fields, tool=tool)


def configured_account_id(config: dict[str, Any], *, tool: str | None = None) -> str:
    return _account_id(config.get("ad_account_id"), tool=tool)
