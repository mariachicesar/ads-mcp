from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from shared.config import Settings, get_settings
from shared.errors import AdsMcpError
from shared.nonce_store import get_nonce_store
from shared.secrets import get_signing_secret


def _parse_timestamp(timestamp: str) -> datetime:
    normalized = timestamp.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical_request(
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    request_id: str,
    body_hash: str,
) -> str:
    return "\n".join(
        [method.upper(), path, timestamp, nonce, request_id, body_hash]
    )


def _sign(secret: str, canonical_request: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_request.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def verify_signed_request(request: Request, settings: Settings) -> None:
    key_id = request.headers.get("X-AdsMcp-Key-Id")
    timestamp = request.headers.get("X-AdsMcp-Timestamp")
    nonce = request.headers.get("X-AdsMcp-Nonce")
    signature = request.headers.get("X-AdsMcp-Signature")
    request_id = request.headers.get("X-Request-Id")

    if not all([key_id, timestamp, nonce, signature, request_id]):
        raise AdsMcpError(
            status_code=401,
            error_code="AUTH_INVALID",
            message="Missing required authentication headers.",
        )

    parsed_timestamp = _parse_timestamp(timestamp)
    now = datetime.now(UTC)
    age_seconds = abs((now - parsed_timestamp).total_seconds())
    if age_seconds > settings.auth_timestamp_tolerance_seconds:
        raise AdsMcpError(
            status_code=401,
            error_code="AUTH_EXPIRED",
            message="Request timestamp is outside the allowed verification window.",
            retryable=True,
        )

    nonce_key = f"{key_id}:{nonce}"
    nonce_store = get_nonce_store(settings)

    secret = get_signing_secret(key_id, settings)
    if not secret:
        raise AdsMcpError(
            status_code=401,
            error_code="AUTH_INVALID",
            message="Signing key is not recognized.",
        )

    body = await request.body()
    canonical_request = _canonical_request(
        method=request.method,
        path=request.url.path,
        timestamp=timestamp,
        nonce=nonce,
        request_id=request_id,
        body_hash=_body_hash(body),
    )
    expected_signature = _sign(secret, canonical_request)
    if not hmac.compare_digest(expected_signature, signature):
        raise AdsMcpError(
            status_code=401,
            error_code="AUTH_INVALID",
            message="Request signature could not be verified.",
        )

    if not nonce_store.put_if_absent(nonce_key, settings.auth_nonce_ttl_seconds):
        raise AdsMcpError(
            status_code=401,
            error_code="AUTH_INVALID",
            message="Request nonce has already been used.",
            retryable=True,
        )

    request.state.request_id = request_id


class SignedRequestMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, service_name: str, settings: Settings | None = None):
        super().__init__(app)
        self.service_name = service_name
        self.settings = settings or get_settings()

    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id")
        if self.settings.require_signed_requests and request.url.path.startswith("/tools/"):
            try:
                await verify_signed_request(request, self.settings)
            except AdsMcpError as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content=exc.to_response(
                        service=self.service_name,
                        request_id=request.headers.get("X-Request-Id"),
                    ),
                )

        return await call_next(request)
