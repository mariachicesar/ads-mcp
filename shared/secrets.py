from __future__ import annotations

import json
import time
from base64 import b64decode
from importlib import import_module
from typing import Any

from shared.config import Settings

_SECRET_CACHE: dict[str, tuple[float, Any]] = {}


def _get_cached(secret_id: str) -> Any | None:
    cached = _SECRET_CACHE.get(secret_id)
    if cached is None:
        return None
    expires_at, value = cached
    if expires_at < time.time():
        _SECRET_CACHE.pop(secret_id, None)
        return None
    return value


def _set_cached(secret_id: str, value: Any, ttl_seconds: int) -> None:
    _SECRET_CACHE[secret_id] = (time.time() + ttl_seconds, value)


def _parse_secret_value(secret_value: dict[str, Any]) -> Any:
    if "SecretString" in secret_value:
        raw_value = secret_value["SecretString"]
    else:
        raw_value = b64decode(secret_value["SecretBinary"]).decode("utf-8")

    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value


def get_secret(secret_id: str, settings: Settings) -> Any:
    cached = _get_cached(secret_id)
    if cached is not None:
        return cached

    boto3 = import_module("boto3")
    client = boto3.client("secretsmanager", region_name=settings.aws_region)
    secret_value = client.get_secret_value(SecretId=secret_id)
    parsed = _parse_secret_value(secret_value)
    _set_cached(secret_id, parsed, settings.secret_cache_ttl_seconds)
    return parsed


def get_signing_secret(key_id: str, settings: Settings) -> str | None:
    if settings.auth_signing_keys_json:
        keys = json.loads(settings.auth_signing_keys_json)
        secret = keys.get(key_id)
        if isinstance(secret, str):
            return secret

    if not settings.aws_region:
        return None

    secret_id = f"{settings.auth_signing_secret_prefix}/{key_id}"
    secret_value = get_secret(secret_id, settings)
    if isinstance(secret_value, dict):
        candidate = secret_value.get("secret") or secret_value.get("value")
        if isinstance(candidate, str):
            return candidate
    if isinstance(secret_value, str):
        return secret_value
    return None


def get_platform_config(platform: str, business_key: str, settings: Settings) -> Any:
    if not settings.aws_region:
        return None
    secret_id = f"/ads-mcp/{business_key}/{platform}/config"
    return get_secret(secret_id, settings)
