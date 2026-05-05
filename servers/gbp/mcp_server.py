"""Google Business Profile MCP server — FastMCP protocol layer.

Exposes GBP tools via the MCP protocol so Claude Desktop can manage listings,
respond to reviews, create posts, and upload photos.

Run via stdio (for Claude Desktop):
    python servers/gbp/mcp_server.py

Required env vars (or local-dev-config.json keys per business):
    ADS_MCP_GBP_CONFIGS_JSON  — JSON object keyed by businessKey
    ADS_MCP_REQUIRE_SIGNED_REQUESTS  — set to "false" for local dev

Each business config must include:
    client_id, client_secret, refresh_token  — OAuth2 credentials
    gbp_account_id                           — GBP account ID or "accounts/123"
    gbp_location_id                          — location ID or full resource name
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Callable

from fastmcp import FastMCP
from pydantic import Field

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response, build_change
from shared.runtime_config import load_platform_runtime_config

SERVICE_NAME = "gbp"

# Queue file — persisted on disk so scheduled posts survive restarts
SCHEDULE_FILE = ROOT / "servers" / "gbp" / "scheduled_posts.json"

mcp = FastMCP(
    name="gbp",
    instructions=(
        "Tools for managing Google Business Profile listings. "
        "Use these tools to respond to reviews, create posts, upload photos, "
        "and read listing info for RnR Electrician and GQ Custom Painting. "
        "Always show proposed review replies to the user before posting. "
        "Always use dry_run=true first for any write operation. "
        "GBP posts publish immediately when executed — use scheduled_time to queue "
        "a post for future publishing via run-scheduled-posts.py."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(business_key: str, tool: str) -> dict:
    return load_platform_runtime_config(
        platform="gbp",
        business_key=business_key,
        required_keys=("client_id", "client_secret", "refresh_token", "gbp_location_id"),
        tool=tool,
    )


def _run_tool(tool_name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except AdsMcpError as exc:
        return exc.to_response(service=SERVICE_NAME, request_id=None)
    except Exception as exc:
        return AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message=f"Unexpected error in {tool_name}: {exc}",
            tool=tool_name,
        ).to_response(service=SERVICE_NAME, request_id=None)


def _load_schedule() -> list[dict]:
    if SCHEDULE_FILE.exists():
        return json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    return []


def _save_schedule(queue: list[dict]) -> None:
    SCHEDULE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@mcp.tool()
def gbp_get_location_info(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
) -> dict:
    """Get the current Google Business Profile listing info: name, phone, website, hours, status."""
    def _run():
        from servers.gbp.gbp_client import fetch_location_info
        config = _load_config(business_key, "gbp_get_location_info")
        data = fetch_location_info(config, tool="gbp_get_location_info")
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_get_location_info",
            mode="read",
            business_key=business_key,
            request_id=None,
            summary=f"Location info for {business_key}",
            data=data,
        )
    return _run_tool("gbp_get_location_info", _run)


@mcp.tool()
def gbp_list_reviews(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    filter_reply: Annotated[str, Field(description="Filter: 'all', 'unreplied', or 'replied'")] = "all",
    page_size: Annotated[int, Field(description="Number of reviews to return (max 50)")] = 20,
) -> dict:
    """List reviews for the GBP listing. Use filter_reply='unreplied' to find reviews needing a response."""
    def _run():
        from servers.gbp.gbp_client import list_reviews
        config = _load_config(business_key, "gbp_list_reviews")
        reply_filter = filter_reply if filter_reply in ("replied", "unreplied") else None
        data = list_reviews(
            config,
            page_size=min(page_size, 50),
            filter_reply=reply_filter,
            tool="gbp_list_reviews",
        )
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_list_reviews",
            mode="read",
            business_key=business_key,
            request_id=None,
            summary=f"{data['totalReviews']} reviews for {business_key}",
            data=data,
        )
    return _run_tool("gbp_list_reviews", _run)


@mcp.tool()
def gbp_list_posts(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    page_size: Annotated[int, Field(description="Number of posts to return (max 20)")] = 10,
) -> dict:
    """List existing local posts on the GBP listing."""
    def _run():
        from servers.gbp.gbp_client import list_posts
        config = _load_config(business_key, "gbp_list_posts")
        data = list_posts(config, page_size=min(page_size, 20), tool="gbp_list_posts")
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_list_posts",
            mode="read",
            business_key=business_key,
            request_id=None,
            summary=f"{data['totalPosts']} posts for {business_key}",
            data=data,
        )
    return _run_tool("gbp_list_posts", _run)


@mcp.tool()
def gbp_list_photos(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    page_size: Annotated[int, Field(description="Number of photos to return")] = 20,
) -> dict:
    """List photos currently on the GBP listing."""
    def _run():
        from servers.gbp.gbp_client import list_media
        config = _load_config(business_key, "gbp_list_photos")
        data = list_media(config, page_size=min(page_size, 50), tool="gbp_list_photos")
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_list_photos",
            mode="read",
            business_key=business_key,
            request_id=None,
            summary=f"{data['totalItems']} photos for {business_key}",
            data=data,
        )
    return _run_tool("gbp_list_photos", _run)


# ---------------------------------------------------------------------------
# Write tools — reviews
# ---------------------------------------------------------------------------

@mcp.tool()
def gbp_reply_to_review(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    review_resource_name: Annotated[str, Field(description="Full review resource name from gbp_list_reviews, e.g. 'accounts/123/locations/456/reviews/abc'")],
    reply_text: Annotated[str, Field(description="The reply text to post. Max ~4096 characters.")],
    dry_run: Annotated[bool, Field(description="If true, shows the proposed reply without posting. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Post or update a reply to a Google Business Profile review.

    IMPORTANT: Always call with dry_run=true first to show the reply to the user.
    Only call with dry_run=false after explicit approval.
    """
    def _run():
        from servers.gbp.gbp_client import reply_to_review
        mode = "dry_run" if dry_run else "execute"
        config = _load_config(business_key, "gbp_reply_to_review")

        changes = [
            build_change(
                field="reviewReply",
                label="Review Reply",
                before=None,
                after=reply_text,
                status="proposed" if dry_run else "applied",
            )
        ]

        if dry_run:
            from shared.responses import build_success_response
            import uuid
            response = build_success_response(
                service=SERVICE_NAME,
                tool="gbp_reply_to_review",
                mode=mode,
                business_key=business_key,
                request_id=None,
                summary=f"Proposed reply to review {review_resource_name}",
                changes=changes,
                data={"reviewResourceName": review_resource_name, "proposedReply": reply_text},
                requires_confirmation=True,
            )
            response["approvalId"] = str(uuid.uuid4())
            return response

        if not approval_id:
            raise AdsMcpError(
                status_code=400,
                error_code="BUSINESS_RULE_BLOCKED",
                message="approvalId is required for execute requests.",
                retryable=False,
                tool="gbp_reply_to_review",
            )

        result = reply_to_review(
            config,
            review_resource_name=review_resource_name,
            reply_text=reply_text,
            tool="gbp_reply_to_review",
        )

        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_reply_to_review",
            mode=mode,
            business_key=business_key,
            request_id=None,
            summary=f"Replied to review {review_resource_name}",
            changes=changes,
            data=result,
            executed=True,
        )
    return _run_tool("gbp_reply_to_review", _run)


@mcp.tool()
def gbp_delete_review_reply(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    review_resource_name: Annotated[str, Field(description="Full review resource name from gbp_list_reviews")],
    dry_run: Annotated[bool, Field(description="If true, shows what will be deleted without doing it. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Delete an existing reply on a GBP review."""
    def _run():
        from servers.gbp.gbp_client import delete_review_reply
        mode = "dry_run" if dry_run else "execute"
        config = _load_config(business_key, "gbp_delete_review_reply")

        if dry_run:
            import uuid
            response = build_success_response(
                service=SERVICE_NAME,
                tool="gbp_delete_review_reply",
                mode=mode,
                business_key=business_key,
                request_id=None,
                summary=f"Proposed: delete reply on review {review_resource_name}",
                data={"reviewResourceName": review_resource_name},
                requires_confirmation=True,
            )
            response["approvalId"] = str(uuid.uuid4())
            return response

        if not approval_id:
            raise AdsMcpError(
                status_code=400,
                error_code="BUSINESS_RULE_BLOCKED",
                message="approvalId is required for execute requests.",
                retryable=False,
                tool="gbp_delete_review_reply",
            )

        result = delete_review_reply(
            config,
            review_resource_name=review_resource_name,
            tool="gbp_delete_review_reply",
        )
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_delete_review_reply",
            mode=mode,
            business_key=business_key,
            request_id=None,
            summary=f"Deleted reply on review {review_resource_name}",
            data=result,
            executed=True,
        )
    return _run_tool("gbp_delete_review_reply", _run)


# ---------------------------------------------------------------------------
# Write tools — posts
# ---------------------------------------------------------------------------

@mcp.tool()
def gbp_create_post(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    summary: Annotated[str, Field(description="Post body text. Keep it concise (under 1500 characters).")],
    topic_type: Annotated[str, Field(description="Post type: STANDARD, EVENT, OFFER, PRODUCT")] = "STANDARD",
    call_to_action_type: Annotated[str | None, Field(description="CTA button type: BOOK, ORDER, SHOP, LEARN_MORE, SIGN_UP, CALL, GET_OFFER")] = None,
    call_to_action_url: Annotated[str | None, Field(description="URL for the CTA button")] = None,
    event_title: Annotated[str | None, Field(description="Event title (required for EVENT posts)")] = None,
    event_start_date: Annotated[str | None, Field(description="Event start date as YYYY-MM-DD")] = None,
    event_end_date: Annotated[str | None, Field(description="Event end date as YYYY-MM-DD")] = None,
    offer_coupon_code: Annotated[str | None, Field(description="Coupon code (for OFFER posts)")] = None,
    offer_redeem_url: Annotated[str | None, Field(description="URL to redeem offer online")] = None,
    scheduled_time: Annotated[str | None, Field(description="Optional future publish time as ISO 8601 (e.g. '2026-05-10T09:00:00'). If set, the post is queued and published later by run-scheduled-posts.py. Leave blank to publish immediately.")] = None,
    dry_run: Annotated[bool, Field(description="If true, shows the proposed post without creating it. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Create a new local post on the GBP listing (What's New, Event, Offer, or Product).

    GBP does not support native scheduling — posts publish immediately.
    Pass scheduled_time to queue the post locally; run scripts/run-scheduled-posts.py
    (via cron or systemd timer) to publish queued posts at the right time.

    IMPORTANT: Always use dry_run=true first. Show the post to the user for review
    before calling with dry_run=false.
    """
    def _run():
        from servers.gbp.gbp_client import create_post
        mode = "dry_run" if dry_run else "execute"
        config = _load_config(business_key, "gbp_create_post")

        post_preview = {
            "topicType": topic_type,
            "summary": summary,
            "callToAction": (
                {"actionType": call_to_action_type, "url": call_to_action_url}
                if call_to_action_type and call_to_action_url else None
            ),
            "event": (
                {"title": event_title, "startDate": event_start_date, "endDate": event_end_date}
                if event_title else None
            ),
            "offer": (
                {"couponCode": offer_coupon_code, "redeemOnlineUrl": offer_redeem_url}
                if (offer_coupon_code or offer_redeem_url) else None
            ),
        }
        if scheduled_time:
            post_preview["scheduledTime"] = scheduled_time

        if dry_run:
            response = build_success_response(
                service=SERVICE_NAME,
                tool="gbp_create_post",
                mode=mode,
                business_key=business_key,
                request_id=None,
                summary=(
                    f"Proposed {topic_type} post for {business_key}"
                    + (f" — scheduled {scheduled_time}" if scheduled_time else " — publishes immediately")
                ),
                data={"proposedPost": post_preview},
                requires_confirmation=True,
            )
            response["approvalId"] = str(uuid.uuid4())
            return response

        if not approval_id:
            raise AdsMcpError(
                status_code=400,
                error_code="BUSINESS_RULE_BLOCKED",
                message="approvalId is required for execute requests.",
                retryable=False,
                tool="gbp_create_post",
            )

        # ── Scheduled path ────────────────────────────────────────────────
        if scheduled_time:
            queue = _load_schedule()
            entry = {
                "id": str(uuid.uuid4()),
                "businessKey": business_key,
                "scheduledTime": scheduled_time,
                "queuedAt": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "summary": summary,
                    "topic_type": topic_type,
                    "call_to_action_type": call_to_action_type,
                    "call_to_action_url": call_to_action_url,
                    "event_title": event_title,
                    "event_start_date": event_start_date,
                    "event_end_date": event_end_date,
                    "offer_coupon_code": offer_coupon_code,
                    "offer_redeem_online_url": offer_redeem_url,
                },
            }
            queue.append(entry)
            _save_schedule(queue)
            return build_success_response(
                service=SERVICE_NAME,
                tool="gbp_create_post",
                mode="scheduled",
                business_key=business_key,
                request_id=None,
                summary=f"Post queued for {scheduled_time} — run scripts/run-scheduled-posts.py to publish",
                data={"scheduledPost": entry},
                executed=True,
            )

        # ── Immediate publish path ────────────────────────────────────────
        result = create_post(
            config,
            summary=summary,
            topic_type=topic_type,
            call_to_action_type=call_to_action_type,
            call_to_action_url=call_to_action_url,
            event_title=event_title,
            event_start_date=event_start_date,
            event_end_date=event_end_date,
            offer_coupon_code=offer_coupon_code,
            offer_redeem_online_url=offer_redeem_url,
            tool="gbp_create_post",
        )
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_create_post",
            mode=mode,
            business_key=business_key,
            request_id=None,
            summary=f"Created {topic_type} post for {business_key}",
            data=result,
            executed=True,
        )
    return _run_tool("gbp_create_post", _run)


@mcp.tool()
def gbp_list_scheduled_posts(
    business_key: Annotated[str | None, Field(description="Filter by business key, or omit for all businesses.")] = None,
) -> dict:
    """List all locally queued scheduled posts waiting to be published."""
    queue = _load_schedule()
    if business_key:
        queue = [e for e in queue if e.get("businessKey") == business_key]
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "tool": "gbp_list_scheduled_posts",
        "total": len(queue),
        "scheduledPosts": queue,
    }


@mcp.tool()
def gbp_cancel_scheduled_post(
    schedule_id: Annotated[str, Field(description="The 'id' field from gbp_list_scheduled_posts.")],
) -> dict:
    """Cancel a queued scheduled post (removes it from the queue without publishing)."""
    queue = _load_schedule()
    before = len(queue)
    queue = [e for e in queue if e.get("id") != schedule_id]
    if len(queue) == before:
        return AdsMcpError(
            status_code=404,
            error_code="NOT_FOUND",
            message=f"No scheduled post found with id '{schedule_id}'.",
            tool="gbp_cancel_scheduled_post",
        ).to_response(service=SERVICE_NAME, request_id=None)
    _save_schedule(queue)
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "tool": "gbp_cancel_scheduled_post",
        "cancelled": schedule_id,
        "remaining": len(queue),
    }


@mcp.tool()
def gbp_delete_post(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    post_resource_name: Annotated[str, Field(description="Full post resource name from gbp_list_posts")],
    dry_run: Annotated[bool, Field(description="If true, shows what will be deleted without doing it.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Delete a local post from the GBP listing."""
    def _run():
        from servers.gbp.gbp_client import delete_post
        mode = "dry_run" if dry_run else "execute"
        config = _load_config(business_key, "gbp_delete_post")

        if dry_run:
            import uuid
            response = build_success_response(
                service=SERVICE_NAME,
                tool="gbp_delete_post",
                mode=mode,
                business_key=business_key,
                request_id=None,
                summary=f"Proposed: delete post {post_resource_name}",
                data={"postResourceName": post_resource_name},
                requires_confirmation=True,
            )
            response["approvalId"] = str(uuid.uuid4())
            return response

        if not approval_id:
            raise AdsMcpError(
                status_code=400,
                error_code="BUSINESS_RULE_BLOCKED",
                message="approvalId is required for execute requests.",
                retryable=False,
                tool="gbp_delete_post",
            )

        result = delete_post(config, post_resource_name=post_resource_name, tool="gbp_delete_post")
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_delete_post",
            mode=mode,
            business_key=business_key,
            request_id=None,
            summary=f"Deleted post {post_resource_name}",
            data=result,
            executed=True,
        )
    return _run_tool("gbp_delete_post", _run)


# ---------------------------------------------------------------------------
# Write tools — photos
# ---------------------------------------------------------------------------

@mcp.tool()
def gbp_upload_photo(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    file_path: Annotated[str, Field(description="Absolute path to the image file (JPEG or PNG) on the local machine")],
    category: Annotated[str, Field(description="Photo category: PROFILE, COVER, LOGO, EXTERIOR, INTERIOR, PRODUCT, AT_WORK, FOOD_AND_DRINK, ADDITIONAL")] = "ADDITIONAL",
    dry_run: Annotated[bool, Field(description="If true, validates the file and shows what will be uploaded without uploading.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Upload a photo to the GBP listing.

    IMPORTANT: Always use dry_run=true first to validate the file and confirm
    category before uploading.
    """
    def _run():
        from pathlib import Path as _Path
        import mimetypes as _mt
        mode = "dry_run" if dry_run else "execute"
        config = _load_config(business_key, "gbp_upload_photo")

        path = _Path(file_path)
        if not path.exists():
            raise AdsMcpError(
                status_code=400,
                error_code="REQUEST_INVALID",
                message=f"File not found: {file_path}",
                tool="gbp_upload_photo",
            )

        mime = _mt.guess_type(str(path))[0] or "unknown"
        size_kb = round(path.stat().st_size / 1024, 1)

        if dry_run:
            import uuid
            response = build_success_response(
                service=SERVICE_NAME,
                tool="gbp_upload_photo",
                mode=mode,
                business_key=business_key,
                request_id=None,
                summary=f"Proposed: upload {path.name} as {category}",
                data={
                    "filePath": str(path),
                    "fileName": path.name,
                    "mimeType": mime,
                    "sizeKB": size_kb,
                    "category": category,
                },
                requires_confirmation=True,
            )
            response["approvalId"] = str(uuid.uuid4())
            return response

        if not approval_id:
            raise AdsMcpError(
                status_code=400,
                error_code="BUSINESS_RULE_BLOCKED",
                message="approvalId is required for execute requests.",
                retryable=False,
                tool="gbp_upload_photo",
            )

        from servers.gbp.gbp_client import upload_photo
        result = upload_photo(
            config,
            file_path=file_path,
            category=category,
            tool="gbp_upload_photo",
        )
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_upload_photo",
            mode=mode,
            business_key=business_key,
            request_id=None,
            summary=f"Uploaded {path.name} as {category}",
            data=result,
            executed=True,
        )
    return _run_tool("gbp_upload_photo", _run)


@mcp.tool()
def gbp_delete_photo(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    media_resource_name: Annotated[str, Field(description="Full media resource name from gbp_list_photos")],
    dry_run: Annotated[bool, Field(description="If true, shows what will be deleted without doing it.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Delete a photo from the GBP listing."""
    def _run():
        from servers.gbp.gbp_client import delete_media
        mode = "dry_run" if dry_run else "execute"
        config = _load_config(business_key, "gbp_delete_photo")

        if dry_run:
            import uuid
            response = build_success_response(
                service=SERVICE_NAME,
                tool="gbp_delete_photo",
                mode=mode,
                business_key=business_key,
                request_id=None,
                summary=f"Proposed: delete media {media_resource_name}",
                data={"mediaResourceName": media_resource_name},
                requires_confirmation=True,
            )
            response["approvalId"] = str(uuid.uuid4())
            return response

        if not approval_id:
            raise AdsMcpError(
                status_code=400,
                error_code="BUSINESS_RULE_BLOCKED",
                message="approvalId is required for execute requests.",
                retryable=False,
                tool="gbp_delete_photo",
            )

        result = delete_media(config, media_resource_name=media_resource_name, tool="gbp_delete_photo")
        return build_success_response(
            service=SERVICE_NAME,
            tool="gbp_delete_photo",
            mode=mode,
            business_key=business_key,
            request_id=None,
            summary=f"Deleted media {media_resource_name}",
            data=result,
            executed=True,
        )
    return _run_tool("gbp_delete_photo", _run)


if __name__ == "__main__":
    mcp.run()
