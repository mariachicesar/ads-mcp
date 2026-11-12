"""Google Business Profile API client helpers.

Uses the Google API Python Client with OAuth2 credentials loaded from config.
Covers these GBP REST APIs:
  - mybusinessbusinessinformation v1  (location info, attributes)
  - My Business v4 (mybusiness.googleapis.com/v4) for reviews, local posts
    and media. Google never published v1 discovery docs for reviews or media
    ("mybusinessreviews"/"mybusinessmedia" v1 don't exist), so these go
    through direct REST calls via _v4_request().
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

from shared.errors import AdsMcpError


# ---------------------------------------------------------------------------
# Credential building
# ---------------------------------------------------------------------------

def _build_credentials(config: dict[str, Any], *, tool: str | None = None):
    """Build OAuth2 credentials from the config dict."""
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="google-auth is not installed.",
            tool=tool,
        ) from exc

    required = ("client_id", "client_secret", "refresh_token")
    for key in required:
        if not config.get(key):
            raise AdsMcpError(
                status_code=500,
                error_code="INTERNAL_ERROR",
                message=f"GBP config missing required field: {key}",
                tool=tool,
            )

    return Credentials(
        token=None,
        refresh_token=config["refresh_token"],
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/business.manage",
        ],
    )


def _build_service(api_name: str, version: str, config: dict[str, Any], *, tool: str | None = None):
    try:
        from googleapiclient.discovery import build as google_build
    except ImportError as exc:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="google-api-python-client is not installed.",
            tool=tool,
        ) from exc

    creds = _build_credentials(config, tool=tool)
    # Several GBP APIs are not listed in the default Google discovery service,
    # so we point directly to each API's own discovery endpoint.
    discovery_url = f"https://{api_name}.googleapis.com/$discovery/rest?version={version}"
    return google_build(
        api_name, version, credentials=creds, cache_discovery=False,
        discoveryServiceUrl=discovery_url,
    )


def _access_token(config: dict[str, Any], *, tool: str | None = None) -> str:
    from google.auth.transport.requests import Request as GoogleAuthRequest

    creds = _build_credentials(config, tool=tool)
    try:
        creds.refresh(GoogleAuthRequest())
    except Exception as exc:  # noqa: BLE001 - google.auth raises its own exception types
        # Happens strictly before any API request is sent, so callers may safely retry.
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_AUTH_ERROR",
            message=f"Could not refresh the GBP access token: {exc}",
            tool=tool,
            retryable=True,
        ) from exc
    return creds.token


def _v4_request(
    method: str,
    path: str,
    config: dict[str, Any],
    *,
    body: dict | None = None,
    params: dict | None = None,
    tool: str | None = None,
) -> dict:
    """Direct REST call to the My Business v4 API (posts, reviews, media).

    path should be relative to https://mybusiness.googleapis.com/v4/
    e.g. "accounts/123/locations/456/localPosts"
    """
    import requests

    url = f"https://mybusiness.googleapis.com/v4/{path}"
    headers = {
        "Authorization": f"Bearer {_access_token(config, tool=tool)}",
        "Content-Type": "application/json",
    }
    resp = requests.request(method, url, headers=headers, json=body, params=params, timeout=30)
    if not resp.ok:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message=f"GBP v4 API error: {resp.status_code} — url={url} — {resp.text[:300]}",
            tool=tool,
            retryable=resp.status_code >= 500,
        )
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


# Backwards-compatible alias (scripts may import the old name).
_postings_request = _v4_request


# ---------------------------------------------------------------------------
# Location helpers
# ---------------------------------------------------------------------------

def get_account_id(config: dict[str, Any], *, tool: str | None = None) -> str:
    """Return the GBP account ID (e.g. 'accounts/123456789')."""
    account_id = config.get("gbp_account_id")
    if not account_id:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="gbp_account_id is not configured for this business.",
            tool=tool,
        )
    # Normalise — allow passing bare number or full resource name
    if not account_id.startswith("accounts/"):
        account_id = f"accounts/{account_id}"
    return account_id


def get_location_name(config: dict[str, Any], *, tool: str | None = None) -> str:
    """Return the GBP location resource name (e.g. 'accounts/123/locations/456')."""
    location_id = config.get("gbp_location_id")
    if not location_id:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="gbp_location_id is not configured for this business.",
            tool=tool,
        )
    account_id = get_account_id(config, tool=tool)
    if location_id.startswith("accounts/"):
        return location_id
    return f"{account_id}/locations/{location_id}"


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def fetch_location_info(config: dict[str, Any], *, tool: str | None = None) -> dict[str, Any]:
    service = _build_service("mybusinessbusinessinformation", "v1", config, tool=tool)
    # The Business Information API (v1) names locations "locations/{id}" with no
    # account prefix, unlike the v4 posts API that get_location_name() serves.
    location_name = "locations/" + get_location_name(config, tool=tool).rsplit("/", 1)[-1]
    try:
        result = (
            service.locations()
            # Business Information v1 has no "businessStatus" field — open/closed
            # status lives under openInfo.status.
            .get(name=location_name, readMask="name,title,phoneNumbers,websiteUri,regularHours,openInfo")
            .execute()
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GBP location fetch failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc
    return result


def list_reviews(
    config: dict[str, Any],
    *,
    page_size: int = 20,
    order_by: str = "updateTime desc",
    filter_reply: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    """List reviews for the location. filter_reply: 'replied' | 'unreplied' | None."""
    location_name = get_location_name(config, tool=tool)
    result = _v4_request(
        "GET", f"{location_name}/reviews", config,
        params={"pageSize": page_size, "orderBy": order_by}, tool=tool,
    )

    reviews = result.get("reviews", [])

    if filter_reply == "unreplied":
        reviews = [r for r in reviews if not r.get("reviewReply")]
    elif filter_reply == "replied":
        reviews = [r for r in reviews if r.get("reviewReply")]

    normalised = []
    for r in reviews:
        reviewer = r.get("reviewer", {})
        normalised.append({
            "reviewId": r.get("reviewId"),
            "resourceName": r.get("name"),
            "reviewer": reviewer.get("displayName", "Anonymous"),
            "starRating": r.get("starRating"),
            "comment": r.get("comment", ""),
            "createTime": r.get("createTime"),
            "updateTime": r.get("updateTime"),
            "hasReply": bool(r.get("reviewReply")),
            "replyText": r.get("reviewReply", {}).get("comment", "") if r.get("reviewReply") else None,
            "replyUpdateTime": r.get("reviewReply", {}).get("updateTime") if r.get("reviewReply") else None,
        })

    return {
        "locationName": location_name,
        "totalReviews": len(normalised),
        "averageRating": result.get("averageRating"),
        "totalReviewCount": result.get("totalReviewCount"),
        "reviews": normalised,
        "nextPageToken": result.get("nextPageToken"),
    }


def list_posts(
    config: dict[str, Any],
    *,
    page_size: int = 20,
    tool: str | None = None,
) -> dict[str, Any]:
    """List existing local posts."""
    location_name = get_location_name(config, tool=tool)
    result = _v4_request(
        "GET", f"{location_name}/localPosts",
        config, params={"pageSize": page_size}, tool=tool,
    )

    posts = result.get("localPosts", [])
    normalised = []
    for p in posts:
        normalised.append({
            "resourceName": p.get("name"),
            "topicType": p.get("topicType"),
            "languageCode": p.get("languageCode"),
            "summary": p.get("summary", ""),
            "callToAction": p.get("callToAction"),
            "createTime": p.get("createTime"),
            "updateTime": p.get("updateTime"),
            "state": p.get("state"),
            "searchUrl": p.get("searchUrl"),
            "media": [m.get("googleUrl") for m in p.get("media", []) if m.get("googleUrl")],
        })

    return {
        "locationName": location_name,
        "totalPosts": len(normalised),
        "posts": normalised,
        "nextPageToken": result.get("nextPageToken"),
    }


def list_media(
    config: dict[str, Any],
    *,
    page_size: int = 20,
    category: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    """List photos/videos on the listing."""
    location_name = get_location_name(config, tool=tool)
    result = _v4_request(
        "GET", f"{location_name}/media", config,
        params={"pageSize": page_size}, tool=tool,
    )

    items = result.get("mediaItems", [])
    if category:
        items = [m for m in items if m.get("mediaFormat") == category.upper() or m.get("locationAssociation", {}).get("category") == category.upper()]

    normalised = []
    for m in items:
        normalised.append({
            "resourceName": m.get("name"),
            "mediaFormat": m.get("mediaFormat"),
            "category": m.get("locationAssociation", {}).get("category"),
            "googleUrl": m.get("googleUrl"),
            "thumbnailUrl": m.get("thumbnailUrl"),
            "createTime": m.get("createTime"),
            "dimensions": m.get("dimensions"),
        })

    return {
        "locationName": location_name,
        "totalItems": len(normalised),
        "mediaItems": normalised,
    }


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def reply_to_review(
    config: dict[str, Any],
    *,
    review_resource_name: str,
    reply_text: str,
    tool: str | None = None,
) -> dict[str, Any]:
    """Post or update a reply to a review (v4: PUT {review}/reply)."""
    result = _v4_request(
        "PUT", f"{review_resource_name}/reply", config,
        body={"comment": reply_text}, tool=tool,
    )
    return {
        "reviewResourceName": review_resource_name,
        "replyText": result.get("comment", reply_text),
        "updateTime": result.get("updateTime"),
    }


def delete_review_reply(
    config: dict[str, Any],
    *,
    review_resource_name: str,
    tool: str | None = None,
) -> dict[str, Any]:
    """Delete the reply on a review (v4: DELETE {review}/reply)."""
    _v4_request("DELETE", f"{review_resource_name}/reply", config, tool=tool)
    return {"reviewResourceName": review_resource_name, "deleted": True}


_SPANISH_HINTS = {
    "el", "la", "los", "las", "de", "del", "que", "para", "con", "por", "una", "un",
    "su", "sus", "y", "es", "en", "nuestra", "nuestro", "mariachi", "canciones",
}
_ENGLISH_HINTS = {
    "the", "and", "for", "with", "your", "our", "you", "to", "of", "is", "on", "we",
}


def detect_language_code(text: str) -> str:
    """Best-effort 'es' vs 'en' for a post body. Spanish punctuation/accents win;
    otherwise compare common-word hits. Defaults to 'en'."""
    if any(ch in text for ch in "¿¡ñÑ"):
        return "es"
    words = [w.strip(".,!?:;()\"'").lower() for w in text.split()]
    es = sum(w in _SPANISH_HINTS for w in words) + sum(ch in "áéíóú" for ch in text.lower())
    en = sum(w in _ENGLISH_HINTS for w in words)
    return "es" if es > en else "en"


def create_post(
    config: dict[str, Any],
    *,
    summary: str,
    topic_type: str = "STANDARD",
    call_to_action_type: str | None = None,
    call_to_action_url: str | None = None,
    event_title: str | None = None,
    event_start_date: str | None = None,
    event_end_date: str | None = None,
    offer_coupon_code: str | None = None,
    offer_redeem_online_url: str | None = None,
    language_code: str | None = None,
    media_url: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    """Create a new local post on the GBP listing.

    language_code defaults to auto-detection ('es' or 'en') from the summary."""
    location_name = get_location_name(config, tool=tool)
    language_code = language_code or detect_language_code(summary)

    body: dict[str, Any] = {
        "languageCode": language_code,
        "summary": summary,
        "topicType": topic_type,
    }

    if call_to_action_type:
        cta: dict[str, Any] = {"actionType": call_to_action_type}
        if call_to_action_type != "CALL" and call_to_action_url:
            cta["url"] = call_to_action_url
        body["callToAction"] = cta

    if media_url:
        body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": media_url}]

    if topic_type == "EVENT" and event_title:
        body["event"] = {
            "title": event_title,
            "schedule": {
                "startDate": _parse_date(event_start_date),
                "endDate": _parse_date(event_end_date or event_start_date),
            },
        }

    if topic_type == "OFFER":
        offer: dict[str, Any] = {}
        if offer_coupon_code:
            offer["couponCode"] = offer_coupon_code
        if offer_redeem_online_url:
            offer["redeemOnlineUrl"] = offer_redeem_online_url
        if offer:
            body["offer"] = offer

    result = _v4_request(
        "POST", f"{location_name}/localPosts",
        config, body=body, tool=tool,
    )

    return {
        "resourceName": result.get("name"),
        "topicType": result.get("topicType"),
        "summary": result.get("summary"),
        "state": result.get("state"),
        "createTime": result.get("createTime"),
        "searchUrl": result.get("searchUrl"),
    }


def delete_post(
    config: dict[str, Any],
    *,
    post_resource_name: str,
    tool: str | None = None,
) -> dict[str, Any]:
    """Delete a local post."""
    # post_resource_name is e.g. "locations/123/localPosts/456" — use it as the path directly
    path = post_resource_name.split("v1/")[-1] if "v1/" in post_resource_name else post_resource_name
    _v4_request("DELETE", path, config, tool=tool)
    return {"postResourceName": post_resource_name, "deleted": True}


def upload_photo(
    config: dict[str, Any],
    *,
    file_path: str,
    category: str = "ADDITIONAL",
    tool: str | None = None,
) -> dict[str, Any]:
    """Upload a photo to the GBP listing.

    category: PROFILE, COVER, LOGO, EXTERIOR, INTERIOR, PRODUCT, AT_WORK,
              FOOD_AND_DRINK, MENU, COMMON_AREA, ROOMS, TEAMS, ADDITIONAL
    """
    path = Path(file_path)
    if not path.exists():
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"File not found: {file_path}",
            tool=tool,
        )

    import requests

    mime_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    location_name = get_location_name(config, tool=tool)

    # v4 byte upload is three steps: startUpload -> upload bytes -> create media item.
    start = _v4_request("POST", f"{location_name}/media:startUpload", config, body={}, tool=tool)
    data_ref = start.get("resourceName")
    if not data_ref:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GBP photo upload failed: startUpload returned no resourceName.",
            tool=tool,
            retryable=True,
        )

    upload_url = f"https://mybusiness.googleapis.com/upload/v1/media/{data_ref}?upload_type=media"
    resp = requests.post(
        upload_url,
        headers={
            "Authorization": f"Bearer {_access_token(config, tool=tool)}",
            "Content-Type": mime_type,
        },
        data=path.read_bytes(),
        timeout=120,
    )
    if not resp.ok:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message=f"GBP photo byte upload failed: {resp.status_code} — {resp.text[:300]}",
            tool=tool,
            retryable=resp.status_code >= 500,
        )

    result = _v4_request(
        "POST", f"{location_name}/media", config,
        body={
            "mediaFormat": "PHOTO",
            "locationAssociation": {"category": category},
            "dataRef": {"resourceName": data_ref},
        },
        tool=tool,
    )

    return {
        "resourceName": result.get("name"),
        "mediaFormat": result.get("mediaFormat"),
        "category": result.get("locationAssociation", {}).get("category"),
        "googleUrl": result.get("googleUrl"),
        "createTime": result.get("createTime"),
    }


def delete_media(
    config: dict[str, Any],
    *,
    media_resource_name: str,
    tool: str | None = None,
) -> dict[str, Any]:
    """Delete a photo or video from the listing (v4: DELETE {media name})."""
    _v4_request("DELETE", media_resource_name, config, tool=tool)
    return {"mediaResourceName": media_resource_name, "deleted": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(date_str: str | None) -> dict[str, int] | None:
    """Convert 'YYYY-MM-DD' to GBP Date object."""
    if not date_str:
        return None
    parts = date_str.split("-")
    if len(parts) != 3:
        return None
    return {"year": int(parts[0]), "month": int(parts[1]), "day": int(parts[2])}
