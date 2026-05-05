"""Google Business Profile API client helpers.

Uses the Google API Python Client with OAuth2 credentials loaded from config.
Covers three GBP REST APIs:
  - mybusinessbusinessinformation  (location info, attributes)
  - mybusinessreviews              (list + reply to reviews)
  - mybusinesspostings             (local posts)
  - mybusinessmedia                (photo uploads)
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


def _postings_request(
    method: str,
    path: str,
    config: dict[str, Any],
    *,
    body: dict | None = None,
    params: dict | None = None,
    tool: str | None = None,
) -> dict:
    """Direct REST call to the My Business v4 API for local posts.

    path should be relative to https://mybusiness.googleapis.com/v4/
    e.g. "accounts/123/locations/456/localPosts"
    """
    import requests
    from google.auth.transport.requests import Request as GoogleAuthRequest

    creds = _build_credentials(config, tool=tool)
    creds.refresh(GoogleAuthRequest())

    url = f"https://mybusiness.googleapis.com/v4/{path}"
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }
    resp = requests.request(method, url, headers=headers, json=body, params=params, timeout=30)
    if not resp.ok:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message=f"GBP postings API error: {resp.status_code} — url={url} — {resp.text[:300]}",
            tool=tool,
            retryable=resp.status_code >= 500,
        )
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


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
    location_name = get_location_name(config, tool=tool)
    try:
        result = (
            service.locations()
            .get(name=location_name, readMask="name,title,phoneNumbers,websiteUri,regularHours,businessStatus")
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
    service = _build_service("mybusinessreviews", "v1", config, tool=tool)
    location_name = get_location_name(config, tool=tool)
    try:
        req = service.locations().reviews().list(
            parent=location_name,
            pageSize=page_size,
            orderBy=order_by,
        )
        result = req.execute()
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GBP review listing failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

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
    result = _postings_request(
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
    service = _build_service("mybusinessmedia", "v1", config, tool=tool)
    location_name = get_location_name(config, tool=tool)
    try:
        result = service.locations().media().list(
            parent=location_name,
            pageSize=page_size,
        ).execute()
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GBP media listing failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

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
    """Post or update a reply to a review."""
    service = _build_service("mybusinessreviews", "v1", config, tool=tool)
    try:
        result = service.locations().reviews().updateReply(
            name=review_resource_name,
            body={"comment": reply_text},
        ).execute()
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GBP review reply failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc
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
    """Delete the reply on a review."""
    service = _build_service("mybusinessreviews", "v1", config, tool=tool)
    try:
        service.locations().reviews().deleteReply(name=review_resource_name).execute()
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GBP review reply deletion failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc
    return {"reviewResourceName": review_resource_name, "deleted": True}


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
    language_code: str = "en",
    tool: str | None = None,
) -> dict[str, Any]:
    """Create a new local post on the GBP listing."""
    location_name = get_location_name(config, tool=tool)

    body: dict[str, Any] = {
        "languageCode": language_code,
        "summary": summary,
        "topicType": topic_type,
    }

    if call_to_action_type and call_to_action_url:
        body["callToAction"] = {
            "actionType": call_to_action_type,
            "url": call_to_action_url,
        }

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

    result = _postings_request(
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
    _postings_request("DELETE", path, config, tool=tool)
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

    mime_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    service = _build_service("mybusinessmedia", "v1", config, tool=tool)
    location_name = get_location_name(config, tool=tool)

    try:
        from googleapiclient.http import MediaFileUpload

        media_body = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)
        result = service.locations().media().create(
            parent=location_name,
            body={
                "mediaFormat": "PHOTO",
                "locationAssociation": {"category": category},
            },
            media_body=media_body,
        ).execute()
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GBP photo upload failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

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
    """Delete a photo or video from the listing."""
    service = _build_service("mybusinessmedia", "v1", config, tool=tool)
    try:
        service.locations().media().delete(name=media_resource_name).execute()
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GBP media deletion failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc
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
