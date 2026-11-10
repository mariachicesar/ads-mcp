"""Publish an Instagram Reel with the Content Publishing API.

container (POST /{ig}/media) → poll status_code until FINISHED → POST /{ig}/media_publish.
Instagram fetches the video from `video_url` (a presigned S3 URL), so nothing is
uploaded from this process.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from shared.errors import AdsMcpError
from shared.meta_ads_client import _graph_request

FAILED_STATUSES = {"ERROR", "EXPIRED"}


def publish_reel(
    config: dict[str, Any], *, ig_user_id: str, video_url: str, caption: str, share_to_feed: bool,
    sleep: Callable[[float], None] = time.sleep, poll_seconds: int = 10, timeout_seconds: int = 300,
) -> dict[str, str]:
    tool = "publish_instagram_reel"
    container = _graph_request(config, "POST", f"{ig_user_id}/media", tool=tool, data={
        "media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": share_to_feed,
    })
    container_id = str(container.get("id"))

    status = None
    for _ in range(max(1, timeout_seconds // poll_seconds)):
        status = _graph_request(config, "GET", container_id, params={"fields": "status_code"}, tool=tool).get("status_code")
        if status == "FINISHED":
            break
        if status in FAILED_STATUSES:
            raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool=tool,
                              message=f"Instagram could not process the Reel (container status {status}).",
                              details={"containerId": container_id, "containerStatus": status})
        sleep(poll_seconds)
    else:
        raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool=tool, retryable=False,
                          message=f"Instagram did not finish processing the Reel within {timeout_seconds}s.",
                          details={"containerId": container_id, "containerStatus": status})

    published = _graph_request(config, "POST", f"{ig_user_id}/media_publish", data={"creation_id": container_id}, tool=tool)
    return {"containerId": container_id, "mediaId": str(published.get("id"))}
