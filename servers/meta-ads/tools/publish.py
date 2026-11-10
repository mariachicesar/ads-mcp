"""Meta video publishing tools: ad-library uploads and scheduled Page videos/Reels.

Same contract as tools/write.py: a call without approvalId returns a dry-run
preview plus an approvalId; calling again with it executes. Approvals for local
files are bound to the file's SHA-256, so editing the file after the dry run
invalidates the approval. Facebook holds scheduled posts itself
(published=false + scheduled_publish_time), so nothing is queued here.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from shared.meta_ads_client import configured_account_id, get_page_access_token, post_video
from shared.models import ToolRequest
from shared.responses import build_change, build_rule_check
from shared.runtime_config import load_meta_ads_config
from tools.write import _invalid, _required, _write_flow

MAX_VIDEO_BYTES = 1_073_741_824  # 1 GiB: Meta's non-resumable upload limit
VIDEO_EXTENSIONS = {".mp4", ".mov"}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _video_source(payload: dict[str, Any], tool: str) -> tuple[dict[str, Any], str | None]:
    """Return ({"filePath": ...} | {"fileUrl": ...}, sha256 or None) after validating the source."""
    file_path, file_url = payload.get("filePath"), payload.get("fileUrl")
    if bool(file_path) == bool(file_url):
        raise _invalid("Provide exactly one of filePath or fileUrl.", tool)
    if file_url:
        if not str(file_url).lower().startswith("https://"):
            raise _invalid("fileUrl must be a public https:// URL.", tool)
        return {"fileUrl": str(file_url)}, None

    path = str(file_path)
    if not os.path.isfile(path):
        raise _invalid(f"filePath {path} does not exist or is not a file.", tool)
    if os.path.splitext(path)[1].lower() not in VIDEO_EXTENSIONS:
        raise _invalid(f"filePath must be one of {sorted(VIDEO_EXTENSIONS)}.", tool)
    size = os.path.getsize(path)
    if size > MAX_VIDEO_BYTES:
        raise _invalid(f"Video is {size} bytes; the limit is {MAX_VIDEO_BYTES} (1 GiB).", tool)
    return {"filePath": path, "bytes": size}, _sha256(path)


def _file_preview(source: dict[str, Any], sha: str | None) -> dict[str, Any]:
    if "fileUrl" in source:
        return {"url": source["fileUrl"]}
    return {"path": source["filePath"], "bytes": source["bytes"], "sha256": sha}


def _bound(request: ToolRequest, sha: str | None) -> dict[str, Any] | None:
    return {**request.payload, "fileSha256": sha} if sha else None


def upload_ad_video(request: ToolRequest, request_id: str | None) -> dict:
    tool = "upload_ad_video"
    payload = request.payload
    name = _required(payload, "name", tool)
    source, sha = _video_source(payload, tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    account_id = configured_account_id(config, tool=tool)
    path = f"{account_id}/advideos"
    fields = {"name": name}
    file = _file_preview(source, sha)
    rule_checks = [build_rule_check(rule="account-ownership", passed=True, message=f"Uploads to the configured ad account {account_id}.")]
    changes = [build_change(field="advideo", label="New ad video", before=None, after={**fields, "file": file}, resource_type="advideo")]

    def execute() -> dict[str, Any]:
        result = post_video(config, path=path, fields=fields, file_path=source.get("filePath"),
                            file_url=source.get("fileUrl"), tool=tool)
        return {"videoId": result.get("id")}

    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"file": file, "graphRequest": {"method": "POST", "path": path, "fields": fields}},
        dry_summary=f"Would upload video '{name}' to the {account_id} ad Media library.",
        done_summary=f"Uploaded video '{name}' to the {account_id} ad Media library.",
        execute=execute, bound_args=_bound(request, sha),
    )
