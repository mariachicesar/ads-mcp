from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    require_signed_requests: bool
    auth_timestamp_tolerance_seconds: int
    auth_nonce_ttl_seconds: int
    auth_signing_secret_prefix: str
    auth_signing_keys_json: str | None
    redis_url: str | None
    aws_region: str | None
    secret_cache_ttl_seconds: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        require_signed_requests=_env_bool("ADS_MCP_REQUIRE_SIGNED_REQUESTS", False),
        auth_timestamp_tolerance_seconds=_env_int(
            "ADS_MCP_AUTH_TIMESTAMP_TOLERANCE_SECONDS", 300
        ),
        auth_nonce_ttl_seconds=_env_int("ADS_MCP_AUTH_NONCE_TTL_SECONDS", 600),
        auth_signing_secret_prefix=os.getenv(
            "ADS_MCP_SIGNING_SECRET_PREFIX",
            "/ads-mcp/shared/signing/backend-rc-to-ads-mcp",
        ),
        auth_signing_keys_json=os.getenv("ADS_MCP_SIGNING_KEYS_JSON"),
        redis_url=os.getenv("ADS_MCP_REDIS_URL") or os.getenv("REDIS_URL"),
        aws_region=os.getenv("AWS_REGION") or os.getenv("ADS_MCP_AWS_REGION"),
        secret_cache_ttl_seconds=_env_int("ADS_MCP_SECRET_CACHE_TTL_SECONDS", 300),
    )
