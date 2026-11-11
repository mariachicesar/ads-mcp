"""TikTok read-only analytics: account totals and per-video stats (Display API).

Unlike the schedule tools these call TikTok directly. Each call refreshes the
OAuth token with shared.tiktok_client.refresh_access_token, which saves a rotated
refresh token to Secrets Manager before the new access token is used (same rule
as the runner). Tokens are never returned or logged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from shared import secrets, tiktok_client
from shared.config import get_settings
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response

SERVICE_NAME = "tiktok"
PACIFIC = ZoneInfo("America/Los_Angeles")
REQUIRED_KEYS = ("client_key", "client_secret", "refresh_token")
MAX_VIDEOS = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _access_token(business_key: str, tool: str) -> str:
    secret_id = f"/ads-mcp/{business_key}/tiktok/config"
    config = secrets.get_secret(secret_id, get_settings())
    if not isinstance(config, dict) or any(not config.get(k) for k in REQUIRED_KEYS):
        raise AdsMcpError(
            status_code=409, error_code="PLATFORM_CONFIG_INCOMPLETE", tool=tool,
            message=f"TikTok is not connected for '{business_key}'. Run `python scripts/tiktok-auth.py`.",
            details={"businessKey": business_key, "platform": "tiktok"},
        )
    return tiktok_client.refresh_access_token(dict(config), secret_id=secret_id)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _video_row(v: dict[str, Any]) -> dict[str, Any]:
    views, likes = _int(v.get("view_count")), _int(v.get("like_count"))
    comments, shares = _int(v.get("comment_count")), _int(v.get("share_count"))
    created = datetime.fromtimestamp(_int(v.get("create_time")), tz=timezone.utc)
    title = (v.get("title") or v.get("video_description") or "").strip()
    return {
        "id": v.get("id"),
        "hook": title.splitlines()[0][:150] if title else "",
        "publishedPacific": created.astimezone(PACIFIC).isoformat(),
        "durationSec": _int(v.get("duration")),
        "views": views, "likes": likes, "comments": comments, "shares": shares,
        "engagementRatePct": round((likes + comments + shares) / views * 100, 2) if views else 0.0,
        "url": v.get("share_url"),
    }


def get_account_stats(request: ToolRequest, request_id: str | None) -> dict:
    tool = "tiktok_get_account_stats"
    token = _access_token(request.businessKey, tool)
    user = tiktok_client.get_user_stats(token)
    data = {
        "displayName": user.get("display_name"),
        "followers": _int(user.get("follower_count")), "following": _int(user.get("following_count")),
        "totalLikes": _int(user.get("likes_count")), "videoCount": _int(user.get("video_count")),
    }
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"TikTok {data['displayName']}: {data['followers']} followers, "
                f"{data['totalLikes']} likes, {data['videoCount']} videos.",
        data=data, freshness={"state": "live"},
    )


def get_video_stats(request: ToolRequest, request_id: str | None) -> dict:
    tool = "tiktok_get_video_stats"
    payload = request.payload or {}
    days = payload.get("days")
    limit = max(1, min(_int(payload.get("limit") or 20), MAX_VIDEOS))
    if days is not None and not 1 <= _int(days) <= 365:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", tool=tool,
                          message="days must be between 1 and 365.")

    token = _access_token(request.businessKey, tool)
    rows = [_video_row(v) for v in tiktok_client.list_videos(token, limit=limit)]
    if days is not None:
        cutoff = _now() - timedelta(days=_int(days))
        rows = [r for r in rows if datetime.fromisoformat(r["publishedPacific"]) >= cutoff]

    totals = {k: sum(r[k] for r in rows) for k in ("views", "likes", "comments", "shares")}
    totals["videos"] = len(rows)
    totals["avgViewsPerVideo"] = round(totals["views"] / len(rows), 1) if rows else 0.0
    window = f"last {days} days" if days is not None else f"latest {limit}"
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=f"TikTok {window}: {totals['videos']} videos, {totals['views']} views, {totals['likes']} likes.",
        data={"window": window, "totals": totals, "videos": rows,
              "note": "TikTok's API gives lifetime counts per video, not per-day views or traffic sources."},
        freshness={"state": "live"},
    )
