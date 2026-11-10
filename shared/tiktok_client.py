"""TikTok Content Posting API (inbox / FILE_UPLOAD) and OAuth token refresh.

Used only by scripts/run-scheduled-social.py. Access tokens are never stored;
the refresh token rotates and is written back to Secrets Manager before the new
access token is used. Never log or return tokens or the upload URL.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests
from requests import RequestException

from shared import secrets
from shared.config import get_settings
from shared.errors import AdsMcpError

API = "https://open.tiktokapis.com"
TOKEN_URL = f"{API}/v2/oauth/token/"
INIT_URL = f"{API}/v2/post/publish/inbox/video/init/"
STATUS_URL = f"{API}/v2/post/publish/status/fetch/"

MIB = 1024 * 1024
SINGLE_CHUNK_MAX = 64 * MIB
CHUNK_SIZE = 10 * MIB
TIMEOUT = 60
UPLOAD_TIMEOUT = 300
DONE_STATUSES = {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}
RETRYABLE_API_CODES = {"rate_limit_exceeded", "internal_error"}
REAUTH_HINT = "Reconnect TikTok with `python scripts/tiktok-auth.py`."
TOOL = "tiktok_publish"


def _error(message: str, *, retryable: bool, **details: Any) -> AdsMcpError:
    return AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message=message, retryable=retryable,
                       tool=TOOL, details={k: v for k, v in details.items() if v is not None})


def _post(url: str, *, retryable_network: bool = True, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    try:
        resp = requests.post(url, timeout=TIMEOUT, **kwargs)
    except RequestException as exc:
        raise _error(f"Could not reach TikTok ({type(exc).__name__}).", retryable=retryable_network) from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {}


def _api_check(status: int, body: dict[str, Any], what: str, *, retryable: bool) -> dict[str, Any]:
    """Return body["data"] or raise with TikTok's error code (never the request contents)."""
    err = body.get("error") or {}
    code = err.get("code")
    if status < 400 and code == "ok":
        return body.get("data") or {}
    message = f"TikTok {what} failed: {code or f'HTTP {status}'}"
    if err.get("message"):
        message += f" ({err['message']})"
    return_retryable = retryable and (status >= 500 or status == 429 or code in RETRYABLE_API_CODES)
    raise _error(message + ".", retryable=return_retryable, tiktokCode=code, httpStatus=status, logId=err.get("log_id"))


def exchange_code(*, client_key: str, client_secret: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """One-time authorization_code exchange (scripts/tiktok-auth.py). Returns TikTok's token response."""
    status, body = _post(TOKEN_URL, data={
        "client_key": client_key, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    }, headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"})
    if not body.get("refresh_token"):
        raise _error(f"TikTok code exchange failed: {body.get('error') or f'HTTP {status}'} "
                     f"{body.get('error_description') or ''}".rstrip() + ".", retryable=False, logId=body.get("log_id"))
    return body


def refresh_access_token(config: dict[str, Any], *, secret_id: str) -> str:
    """Exchange the stored refresh token for an access token; persist a rotated refresh token first."""
    status, body = _post(TOKEN_URL, data={
        "client_key": config["client_key"], "client_secret": config["client_secret"],
        "grant_type": "refresh_token", "refresh_token": config["refresh_token"],
    }, headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"})
    access_token = body.get("access_token")
    if not access_token:
        code = body.get("error") or f"HTTP {status}"
        description = body.get("error_description") or ""
        retryable = status >= 500 or status == 429
        hint = "" if retryable else f" {REAUTH_HINT}"
        raise _error(f"TikTok token refresh failed: {code} {description}".rstrip() + "." + hint,
                     retryable=retryable, tiktokCode=body.get("error"), httpStatus=status, logId=body.get("log_id"))

    new_refresh = body.get("refresh_token")
    if new_refresh and new_refresh != config["refresh_token"]:
        expires_at = None
        if body.get("refresh_expires_in"):
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(body["refresh_expires_in"]))
                          ).strftime("%Y-%m-%dT%H:%M:%SZ")
        updated = {**config, "refresh_token": new_refresh, "refresh_expires_at": expires_at or config.get("refresh_expires_at")}
        try:
            # Update-only: the runner's EC2 role has PutSecretValue, not CreateSecret.
            secrets.update_secret(secret_id, updated, get_settings())
        except Exception as exc:  # noqa: BLE001 - must not use the token if the rotation was not saved
            raise _error("Could not save TikTok's rotated refresh token; not publishing this run.",
                         retryable=True, reason=type(exc).__name__) from exc
    return str(access_token)


def chunk_plan(size: int) -> list[tuple[int, int]]:
    """Inclusive byte ranges: one chunk up to 64 MiB, else 10 MiB chunks with the remainder in the last."""
    if size <= 0:
        raise _error("The video is empty.", retryable=False)
    if size <= SINGLE_CHUNK_MAX:
        return [(0, size - 1)]
    count = size // CHUNK_SIZE
    ranges = [(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE - 1) for i in range(count - 1)]
    ranges.append(((count - 1) * CHUNK_SIZE, size - 1))
    return ranges


def send_to_inbox(
    access_token: str, *, size: int, read_range: Callable[[int, int], bytes],
    sleep: Callable[[float], None] = time.sleep, poll_seconds: int = 10, timeout_seconds: int = 300,
) -> dict[str, str]:
    """Upload the video to the creator's TikTok inbox as a draft and wait until TikTok delivers it."""
    plan = chunk_plan(size)
    auth = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"}
    chunk_size = plan[0][1] - plan[0][0] + 1
    status, body = _post(INIT_URL, headers=auth, json={"source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": len(plan),
    }})
    data = _api_check(status, body, "upload init", retryable=True)
    publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
    if not publish_id or not upload_url:
        raise _error("TikTok upload init returned no publish_id/upload_url.", retryable=True)

    for start, end in plan:
        chunk = read_range(start, end)
        try:
            resp = requests.put(upload_url, data=chunk, timeout=UPLOAD_TIMEOUT, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {start}-{end}/{size}",
            })
        except RequestException as exc:
            # Nothing reaches the inbox until every chunk lands, so a retry cannot double-post.
            raise _error(f"Video upload to TikTok failed ({type(exc).__name__}).", retryable=True,
                         publishId=publish_id) from exc
        if resp.status_code not in (200, 201, 206):
            raise _error(f"Video upload to TikTok failed: HTTP {resp.status_code}.",
                         retryable=resp.status_code >= 500, publishId=publish_id, httpStatus=resp.status_code)

    # From here the draft may already be on its way: never retry automatically.
    last = None
    for attempt in range(max(1, timeout_seconds // poll_seconds)):
        if attempt:
            sleep(poll_seconds)
        status, body = _post(STATUS_URL, retryable_network=False, headers=auth, json={"publish_id": publish_id})
        row = _api_check(status, body, "status fetch", retryable=False)
        last = row.get("status")
        if last in DONE_STATUSES:
            return {"publishId": publish_id, "status": last}
        if last == "FAILED":
            reason = row.get("fail_reason") or "unknown"
            raise _error(f"TikTok rejected the video: {reason}.", retryable=False, publishId=publish_id, failReason=reason)
    raise _error(f"TikTok did not confirm delivery within {timeout_seconds}s (last status {last}); "
                 "check the TikTok inbox before rescheduling.", retryable=False, publishId=publish_id, lastStatus=last)
