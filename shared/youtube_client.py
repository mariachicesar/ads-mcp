"""YouTube API plumbing shared by the YouTube MCP tools and the EC2 social runner.

Config (platform "youtube"; ADS_MCP_YOUTUBE_CONFIGS_JSON or Secrets Manager
/ads-mcp/<business>/youtube/config): client_id, client_secret, refresh_token
(from scripts/youtube-auth.py), optional channel_id.

Lives in shared/ because every server ships a top-level `tools` package, so the
runner (scripts/run-scheduled-social.py) must not import servers/youtube/tools.
"""

from __future__ import annotations

from typing import Any

from shared.errors import AdsMcpError
from shared.runtime_config import load_platform_runtime_config

TOKEN_URI = "https://oauth2.googleapis.com/token"


def load_config(business_key: str, tool: str | None = None) -> dict[str, Any]:
    return load_platform_runtime_config(
        platform="youtube", business_key=business_key,
        required_keys=("client_id", "client_secret", "refresh_token"), tool=tool,
    )


def _credentials(config: dict[str, Any]):
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None, refresh_token=config["refresh_token"], client_id=config["client_id"],
        client_secret=config["client_secret"], token_uri=TOKEN_URI,
        # No scopes here: sending them on refresh fails with invalid_scope if the stored token was
        # granted a different set; the refresh then returns whatever the user actually granted.
    )


def data_client(config: dict[str, Any]):
    """YouTube Data API v3 client."""
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_credentials(config), cache_discovery=False)


def build_clients(config: dict[str, Any]):
    """Return (data_api, analytics_api) service clients sharing one set of credentials."""
    from googleapiclient.discovery import build

    creds = _credentials(config)
    return (build("youtube", "v3", credentials=creds, cache_discovery=False),
            build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False))


def upstream_error(tool: str, exc: Exception) -> AdsMcpError:
    reason = str(exc)
    hint = ""
    if "insufficient" in reason.lower() or "invalid_grant" in reason.lower():
        hint = " Reconnect with `python scripts/youtube-auth.py`."
    elif "has not been used" in reason or "accessNotConfigured" in reason:
        hint = " Enable the YouTube Data API v3 and YouTube Analytics API in the Google Cloud project."
    return AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool=tool,
                       message="YouTube API request failed." + hint, details={"reason": reason[:500]})


def youtube_status(business_key: str, video_id: str) -> dict[str, Any] | None:
    """privacyStatus, uploadStatus and publishAt of a video, or None if YouTube has no such video.

    API errors propagate unchanged so the caller can tell "not found" from "could not check".
    """
    items = data_client(load_config(business_key, "youtube_status")).videos().list(
        part="status", id=video_id).execute().get("items") or []
    if not items:
        return None
    status = items[0].get("status") or {}
    return {key: status.get(key) for key in ("privacyStatus", "uploadStatus", "publishAt")}
