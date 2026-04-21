from __future__ import annotations

import json
from typing import Any

from shared.config import get_settings
from shared.errors import AdsMcpError
from shared.secrets import get_platform_config


def _platform_env_var_name(platform: str) -> str:
    normalized = platform.replace("-", "_").upper()
    return f"ADS_MCP_{normalized}_CONFIGS_JSON"


def _load_env_platform_configs(platform: str) -> dict[str, Any]:
    import os

    raw_value = os.getenv(_platform_env_var_name(platform))
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message=f"{platform} runtime configuration is invalid.",
            details={"reason": str(exc)},
        ) from exc
    if not isinstance(parsed, dict):
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message=f"{platform} runtime configuration must be a JSON object.",
        )
    return parsed


def load_platform_runtime_config(
    *,
    platform: str,
    business_key: str,
    required_keys: tuple[str, ...] = (),
    tool: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    env_configs = _load_env_platform_configs(platform)

    config: Any | None = env_configs.get(business_key)
    if config is None:
        config = get_platform_config(platform, business_key, settings)

    if config is None:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"No {platform} configuration found for businessKey '{business_key}'.",
            tool=tool,
            details={"businessKey": business_key, "platform": platform},
        )

    if not isinstance(config, dict):
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message=f"{platform} configuration for businessKey '{business_key}' is malformed.",
            tool=tool,
            details={"businessKey": business_key, "platform": platform},
        )

    missing_keys = [key for key in required_keys if not config.get(key)]
    if missing_keys:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message=f"{platform} configuration for businessKey '{business_key}' is incomplete.",
            tool=tool,
            details={
                "businessKey": business_key,
                "platform": platform,
                "missingKeys": missing_keys,
            },
        )

    return config


def load_google_ads_config(*, business_key: str, tool: str | None = None) -> dict[str, Any]:
    return load_platform_runtime_config(
        platform="google-ads",
        business_key=business_key,
        required_keys=("customer_account_id",),
        tool=tool,
    )


def load_google_ads_sdk_config(*, business_key: str, tool: str | None = None) -> dict[str, Any]:
    return load_platform_runtime_config(
        platform="google-ads",
        business_key=business_key,
        required_keys=(
            "customer_account_id",
            "developer_token",
            "client_id",
            "client_secret",
            "refresh_token",
        ),
        tool=tool,
    )
