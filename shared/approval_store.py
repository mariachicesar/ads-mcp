"""Single-use approval tokens for dry-run → execute write flows.

A dry run issues an approvalId bound to a fingerprint of (service, tool,
businessKey, exact arguments). Execute must present the same id with the same
arguments before it expires; the id is consumed on successful use.

Backed by Redis when ADS_MCP_REDIS_URL / REDIS_URL is set (so HTTP workers
share approvals), otherwise by process memory, mirroring shared.nonce_store.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from importlib import import_module
from threading import Lock
from typing import Any

from shared.config import Settings, get_settings
from shared.errors import AdsMcpError

APPROVAL_TTL_SECONDS = 15 * 60
_KEY_PREFIX = "ads-mcp:approval:"


def fingerprint(*, service: str, tool: str, business_key: str, args: dict[str, Any]) -> str:
    normalized = {k: v for k, v in sorted(args.items()) if v is not None}
    canonical = json.dumps(
        {"service": service, "tool": tool, "businessKey": business_key, "args": normalized},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BaseApprovalStore:
    def put(self, approval_id: str, record: dict[str, Any], ttl_seconds: int) -> None:
        raise NotImplementedError

    def get(self, approval_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def delete(self, approval_id: str) -> bool:
        raise NotImplementedError


class InMemoryApprovalStore(BaseApprovalStore):
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = Lock()

    def _cleanup(self) -> None:
        now = time.time()
        for key in [k for k, (expires_at, _) in self._store.items() if expires_at < now]:
            self._store.pop(key, None)

    def put(self, approval_id: str, record: dict[str, Any], ttl_seconds: int) -> None:
        with self._lock:
            self._cleanup()
            self._store[approval_id] = (time.time() + ttl_seconds, record)

    def get(self, approval_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._cleanup()
            entry = self._store.get(approval_id)
            return entry[1] if entry else None

    def delete(self, approval_id: str) -> bool:
        with self._lock:
            return self._store.pop(approval_id, None) is not None


class RedisApprovalStore(BaseApprovalStore):
    def __init__(self, redis_url: str) -> None:
        redis_module = import_module("redis")
        self._client = redis_module.from_url(redis_url, decode_responses=True)

    def put(self, approval_id: str, record: dict[str, Any], ttl_seconds: int) -> None:
        self._client.set(_KEY_PREFIX + approval_id, json.dumps(record), ex=ttl_seconds)

    def get(self, approval_id: str) -> dict[str, Any] | None:
        raw = self._client.get(_KEY_PREFIX + approval_id)
        return json.loads(raw) if raw else None

    def delete(self, approval_id: str) -> bool:
        return bool(self._client.delete(_KEY_PREFIX + approval_id))


_MEMORY_APPROVAL_STORE = InMemoryApprovalStore()
_REDIS_APPROVAL_STORES: dict[str, RedisApprovalStore] = {}


def get_approval_store(settings: Settings | None = None) -> BaseApprovalStore:
    settings = settings or get_settings()
    if settings.redis_url:
        try:
            store = _REDIS_APPROVAL_STORES.get(settings.redis_url)
            if store is None:
                store = RedisApprovalStore(settings.redis_url)
                _REDIS_APPROVAL_STORES[settings.redis_url] = store
            return store
        except Exception:
            return _MEMORY_APPROVAL_STORE
    return _MEMORY_APPROVAL_STORE


def issue_approval(*, service: str, tool: str, business_key: str, args: dict[str, Any], ttl_seconds: int = APPROVAL_TTL_SECONDS) -> dict[str, str]:
    approval_id = str(uuid.uuid4())
    expires_at = time.time() + ttl_seconds
    get_approval_store().put(approval_id, {
        "fingerprint": fingerprint(service=service, tool=tool, business_key=business_key, args=args),
        "tool": tool,
        "expiresAt": expires_at,
    }, ttl_seconds)
    return {
        "approvalId": approval_id,
        "approvalExpiresAt": datetime.fromtimestamp(expires_at, UTC).isoformat().replace("+00:00", "Z"),
    }


def verify_approval(*, approval_id: str, service: str, tool: str, business_key: str, args: dict[str, Any]) -> None:
    """Raise unless approval_id is live and was issued for exactly these arguments."""
    record = get_approval_store().get(approval_id)
    if record is None or record.get("expiresAt", 0) < time.time():
        raise AdsMcpError(
            status_code=409,
            error_code="APPROVAL_INVALID",
            message="approvalId is unknown, already used, or expired (15 minute limit). Run the dry run again.",
            tool=tool,
        )
    if record.get("fingerprint") != fingerprint(service=service, tool=tool, business_key=business_key, args=args):
        raise AdsMcpError(
            status_code=409,
            error_code="APPROVAL_MISMATCH",
            message="approvalId was issued for different arguments. Run the dry run again with the exact arguments to execute.",
            tool=tool,
        )


def consume_approval(approval_id: str) -> None:
    get_approval_store().delete(approval_id)
