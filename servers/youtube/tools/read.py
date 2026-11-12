"""YouTube read tools — YouTube Data API v3 + YouTube Analytics API v2.

Config (platform "youtube"; ADS_MCP_YOUTUBE_CONFIGS_JSON or Secrets Manager
/ads-mcp/<business>/youtube/config): client_id, client_secret, refresh_token
(scopes youtube.readonly + yt-analytics.readonly, from scripts/youtube-auth.py),
optional channel_id (defaults to the authorized user's own channel).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from shared import youtube_client
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response

SERVICE_NAME = "youtube"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
RANGE_DAYS = {"LAST_7_DAYS": 7, "LAST_28_DAYS": 28, "LAST_30_DAYS": 30, "LAST_90_DAYS": 90}
# Analytics dimension name -> label used in the response rows.
DIMENSIONS = {
    "day": "day",
    "traffic_source": "insightTrafficSourceType",
    "video": "video",
    "country": "country",
    "device": "deviceType",
}
METRICS = "views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares,subscribersGained"
# Traffic-source and device reports only support watch metrics (YouTube rejects likes/shares/etc.).
WATCH_METRICS = "views,estimatedMinutesWatched,averageViewDuration"
METRICS_BY_DIMENSION = {"traffic_source": WATCH_METRICS, "device": WATCH_METRICS}


def _today() -> date:
    return date.today()


# Config, clients and error mapping live in shared.youtube_client (also used by the social runner).
_load_config = youtube_client.load_config
_build_clients = youtube_client.build_clients
_upstream = youtube_client.upstream_error


def _channel(data_api, config: dict[str, Any]) -> dict[str, Any]:
    kwargs = {"id": config["channel_id"]} if config.get("channel_id") else {"mine": True}
    items = data_api.channels().list(part="snippet,statistics,contentDetails", **kwargs).execute().get("items") or []
    if not items:
        raise AdsMcpError(status_code=404, error_code="REQUEST_INVALID", tool="youtube",
                          message="No YouTube channel found for this account (check channel_id or the authorized Google account).")
    return items[0]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _response(tool: str, business_key: str, request_id: str | None, summary: str, data: dict) -> dict:
    return build_success_response(service=SERVICE_NAME, tool=tool, mode="read", business_key=business_key,
                                  request_id=request_id, summary=summary, data=data, freshness={"state": "live"})


def get_channel_overview(req: ToolRequest, request_id: str | None) -> dict:
    tool = "youtube_get_channel_overview"
    config = _load_config(req.businessKey, tool)
    try:
        data_api, _ = _build_clients(config)
        ch = _channel(data_api, config)
    except AdsMcpError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _upstream(tool, exc) from exc
    stats = ch.get("statistics") or {}
    data = {
        "channelId": ch.get("id"), "title": (ch.get("snippet") or {}).get("title"),
        "subscribers": None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount")),
        "totalViews": _int(stats.get("viewCount")), "videoCount": _int(stats.get("videoCount")),
    }
    return _response(tool, req.businessKey, request_id,
                     f"YouTube '{data['title']}': {data['subscribers']} subscribers, {data['totalViews']} views, "
                     f"{data['videoCount']} videos.", data)


def get_recent_videos(req: ToolRequest, request_id: str | None) -> dict:
    tool = "youtube_get_recent_videos"
    limit = max(1, min(_int((req.payload or {}).get("limit") or 10), 50))
    config = _load_config(req.businessKey, tool)
    try:
        data_api, _ = _build_clients(config)
        ch = _channel(data_api, config)
        uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        items = data_api.playlistItems().list(part="contentDetails", playlistId=uploads,
                                              maxResults=limit).execute().get("items") or [] if uploads else []
        ids = [i["contentDetails"]["videoId"] for i in items if (i.get("contentDetails") or {}).get("videoId")]
        videos = data_api.videos().list(part="snippet,statistics,contentDetails",
                                        id=",".join(ids)).execute().get("items") or [] if ids else []
    except AdsMcpError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _upstream(tool, exc) from exc

    rows = []
    for v in videos:
        s, sn = v.get("statistics") or {}, v.get("snippet") or {}
        views = _int(s.get("viewCount"))
        likes, comments = _int(s.get("likeCount")), _int(s.get("commentCount"))
        rows.append({
            "videoId": v.get("id"), "title": sn.get("title"), "publishedAt": sn.get("publishedAt"),
            "duration": (v.get("contentDetails") or {}).get("duration"),
            "views": views, "likes": likes, "comments": comments,
            "engagementRatePct": round((likes + comments) / views * 100, 2) if views else 0.0,
            "url": f"https://www.youtube.com/watch?v={v.get('id')}",
        })
    total = sum(r["views"] for r in rows)
    return _response(tool, req.businessKey, request_id,
                     f"YouTube: {len(rows)} recent videos, {total} lifetime views combined.",
                     {"channelId": ch.get("id"), "videos": rows})


def get_analytics(req: ToolRequest, request_id: str | None) -> dict:
    tool = "youtube_get_analytics"
    payload = req.payload or {}
    date_range = str(payload.get("dateRange") or "LAST_28_DAYS").upper()
    dimension = str(payload.get("dimension") or "day")
    if date_range not in RANGE_DAYS:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", tool=tool,
                          message=f"dateRange must be one of {', '.join(RANGE_DAYS)}.")
    if dimension not in DIMENSIONS:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", tool=tool,
                          message=f"dimension must be one of {', '.join(DIMENSIONS)}.")

    # YouTube Analytics lags about two days; end the window there so the last days aren't zero.
    end = _today() - timedelta(days=2)
    start = end - timedelta(days=RANGE_DAYS[date_range] - 1)
    api_dim = DIMENSIONS[dimension]
    query: dict[str, Any] = {"ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
                             "metrics": METRICS_BY_DIMENSION.get(dimension, METRICS), "dimensions": api_dim}
    if dimension == "day":
        query["sort"] = "day"
    else:
        query["sort"] = "-views"
        if dimension == "video":
            query["maxResults"] = 25

    config = _load_config(req.businessKey, tool)
    try:
        _, analytics_api = _build_clients(config)
        result = analytics_api.reports().query(**query).execute()
    except Exception as exc:  # noqa: BLE001
        raise _upstream(tool, exc) from exc

    headers = [h["name"] for h in result.get("columnHeaders") or []]
    rows = [dict(zip(headers, r)) for r in result.get("rows") or []]
    for r in rows:
        if api_dim in r and api_dim != dimension:
            r[dimension] = r.pop(api_dim)
    totals = {m: sum(_int(r.get(m)) for r in rows) for m in ("views", "estimatedMinutesWatched", "likes",
                                                              "comments", "shares", "subscribersGained")}
    return _response(tool, req.businessKey, request_id,
                     f"YouTube {date_range} by {dimension}: {totals['views']} views, "
                     f"{totals['estimatedMinutesWatched']} minutes watched.",
                     {"dateRange": date_range, "dimension": dimension, "dateStart": start.isoformat(),
                      "dateEnd": end.isoformat(), "totals": totals, "rows": rows})
