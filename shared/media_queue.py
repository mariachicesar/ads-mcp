"""S3-backed queue for scheduled social posts (Instagram, TikTok).

Layout in the bucket named by ADS_MCP_MEDIA_BUCKET:
    videos/<businessKey>/<sha256>.<ext>                  the video, stored once per content hash
    images/<businessKey>/<sha256>.jpg                    a still image (GBP post photo), stored once per hash
    queue/<state>/<YYYYMMDDTHHMMZ>-<id>.json             one entry per scheduled post

A state change is copy-to-new-prefix then delete-old. Because `move` reads the
source first and raises KeyError when it is gone, moving pending → processing
doubles as an exclusive claim for the runner.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from shared.errors import AdsMcpError

STATES = ("pending", "processing", "done", "failed", "cancelled")


def _s3_client():
    import boto3

    return boto3.client("s3", region_name=os.getenv("AWS_REGION") or "us-east-1")


def _bucket(bucket: str | None) -> str:
    name = bucket or os.getenv("ADS_MCP_MEDIA_BUCKET")
    if not name:
        raise AdsMcpError(status_code=500, error_code="INTERNAL_ERROR",
                          message="ADS_MCP_MEDIA_BUCKET is not configured.")
    return name


def _is_missing(client: Any, exc: Exception) -> bool:
    if isinstance(exc, getattr(client.exceptions, "NoSuchKey", ())):
        return True
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_key(state: str, entry: dict[str, Any]) -> str:
    when = datetime.strptime(entry["scheduledAt"], "%Y-%m-%dT%H:%M:%SZ")
    return f"queue/{state}/{when:%Y%m%dT%H%MZ}-{entry['id']}.json"


def put_video(*, business_key: str, file_path: str, sha256: str, bucket: str | None = None) -> str:
    """Upload a local video under its content hash; skip the upload if it already exists."""
    name, client = _bucket(bucket), _s3_client()
    ext = os.path.splitext(file_path)[1].lower() or ".mp4"
    key = f"videos/{business_key}/{sha256}{ext}"
    try:
        client.head_object(Bucket=name, Key=key)
        return key
    except Exception as exc:  # noqa: BLE001 - boto raises ClientError(404) for a missing key
        if not _is_missing(client, exc):
            raise
    client.upload_file(file_path, name, key, ExtraArgs={"ContentType": "video/mp4" if ext == ".mp4" else "video/quicktime"})
    return key


def put_image(*, business_key: str, file_path: str, sha256: str, bucket: str | None = None) -> str:
    """Upload a local JPEG (e.g. a GBP post photo) under its content hash; skip it if it already exists."""
    name, client = _bucket(bucket), _s3_client()
    key = f"images/{business_key}/{sha256}.jpg"
    try:
        client.head_object(Bucket=name, Key=key)
        return key
    except Exception as exc:  # noqa: BLE001 - boto raises ClientError(404) for a missing key
        if not _is_missing(client, exc):
            raise
    client.upload_file(file_path, name, key, ExtraArgs={"ContentType": "image/jpeg"})
    return key


def enqueue(entry: dict[str, Any], *, bucket: str | None = None) -> str:
    name = _bucket(bucket)
    body = {**entry, "id": uuid.uuid4().hex, "createdAt": _utc_iso(datetime.now(timezone.utc)),
            "attempts": 0, "lastError": None, "result": None}
    key = _entry_key("pending", body)
    _s3_client().put_object(Bucket=name, Key=key, Body=json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"),
                            ContentType="application/json")
    return key


def list_entries(*, states: tuple[str, ...], business_key: str | None = None, platform: str | None = None,
                 bucket: str | None = None) -> list[dict[str, Any]]:
    name, client = _bucket(bucket), _s3_client()
    rows: list[dict[str, Any]] = []
    for state in states:
        token = None
        while True:
            kwargs = {"Bucket": name, "Prefix": f"queue/{state}/"}
            if token:
                kwargs["ContinuationToken"] = token
            page = client.list_objects_v2(**kwargs)
            for obj in page.get("Contents", []):
                try:
                    raw = client.get_object(Bucket=name, Key=obj["Key"])["Body"].read()
                except Exception as exc:  # noqa: BLE001 - moved by another process between list and get
                    if _is_missing(client, exc):
                        continue
                    raise
                try:
                    entry = json.loads(raw)
                    if not isinstance(entry, dict):
                        raise ValueError("entry is not a JSON object")
                except ValueError as exc:
                    # Unreadable entry: surface it so the runner can quarantine it.
                    rows.append({"_key": obj["Key"], "_state": state, "_invalid": str(exc), "scheduledAt": ""})
                    continue
                if business_key and entry.get("businessKey") != business_key:
                    continue
                if platform and entry.get("platform") != platform:
                    continue
                rows.append({**entry, "_key": obj["Key"], "_state": state})
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
    return sorted(rows, key=lambda r: r.get("scheduledAt", ""))


def move(key: str, to_state: str, *, updates: dict[str, Any] | None = None, bucket: str | None = None) -> str:
    """Move an entry to another state. Raises KeyError if the source no longer exists (lost a claim)."""
    if to_state not in STATES:
        raise ValueError(f"Unknown queue state {to_state!r}.")
    name, client = _bucket(bucket), _s3_client()
    try:
        entry = json.loads(client.get_object(Bucket=name, Key=key)["Body"].read())
    except Exception as exc:  # noqa: BLE001
        if _is_missing(client, exc):
            raise KeyError(key) from exc
        raise
    entry.update(updates or {})
    new_key = _entry_key(to_state, entry)
    extra: dict[str, Any] = {}
    if to_state == "processing":
        # Conditional write: only one runner can create the processing copy.
        extra["IfNoneMatch"] = "*"
    try:
        client.put_object(Bucket=name, Key=new_key, Body=json.dumps(entry, ensure_ascii=False, default=str).encode("utf-8"),
                          ContentType="application/json", **extra)
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if extra and code in {"PreconditionFailed", "ConditionalRequestConflict", "412"}:
            raise KeyError(key) from exc
        raise
    if new_key != key:
        client.delete_object(Bucket=name, Key=key)
    return new_key


def object_size(key: str, *, bucket: str | None = None) -> int:
    return int(_s3_client().head_object(Bucket=_bucket(bucket), Key=key)["ContentLength"])


def read_range(key: str, start: int, end: int, *, bucket: str | None = None) -> bytes:
    """Bytes start..end (inclusive) of an object, read into memory (the EC2 disk is nearly full)."""
    return _s3_client().get_object(Bucket=_bucket(bucket), Key=key, Range=f"bytes={start}-{end}")["Body"].read()


def presigned_get(key: str, *, expires: int = 3600, bucket: str | None = None) -> str:
    return _s3_client().generate_presigned_url(
        "get_object", Params={"Bucket": _bucket(bucket), "Key": key}, ExpiresIn=expires,
    )


def quarantine(key: str, reason: str, *, bucket: str | None = None) -> str:
    """Move an unreadable entry's raw bytes to queue/failed/ (with a .reason.txt) without parsing it."""
    name, client = _bucket(bucket), _s3_client()
    raw = client.get_object(Bucket=name, Key=key)["Body"].read()
    new_key = "queue/failed/" + key.rsplit("/", 1)[-1]
    client.put_object(Bucket=name, Key=new_key, Body=raw)
    client.put_object(Bucket=name, Key=new_key + ".reason.txt", Body=reason.encode("utf-8"))
    client.delete_object(Bucket=name, Key=key)
    return new_key
