from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from shared import secrets as secrets_mod
from shared.config import get_settings
from shared.models import (
    KNOWN_PLATFORMS,
    ClientManifest,
    PlatformReadiness,
    ReadinessReport,
)

__all__ = [
    "KNOWN_PLATFORMS",
    "REQUIRED_KEYS_BY_PLATFORM",
    "INDEX_SECRET_ID",
    "manifest_secret_id",
    "credential_secret_id",
    "load_client_manifest",
    "list_client_manifests",
    "client_readiness",
    "merge_platform_config",
    "upsert_client_manifest",
    "set_platform_config",
    "delete_client_manifest",
]

INDEX_SECRET_ID = "/ads-mcp/_index/clients"

# Full credential-level requirements per platform. Metadata keys
# (customer_account_id, property_id, site_url, gbp_location_id, ad_account_id)
# may be supplied by the manifest OR the credential secret; readiness checks
# the merged view.
REQUIRED_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "google-ads": (
        "customer_account_id",
        "developer_token",
        "client_id",
        "client_secret",
        "refresh_token",
    ),
    "analytics": ("property_id", "client_id", "client_secret", "refresh_token"),
    "search-console": ("site_url", "client_id", "client_secret", "refresh_token"),
    "gbp": ("client_id", "client_secret", "refresh_token", "gbp_location_id"),
    "meta-ads": ("ad_account_id", "access_token"),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _clients_dir() -> Path:
    override = os.getenv("ADS_MCP_CLIENTS_DIR")
    return Path(override) if override else _repo_root() / "clients"


def manifest_secret_id(business_key: str) -> str:
    return f"/ads-mcp/{business_key}/manifest"


def credential_secret_id(business_key: str, platform: str) -> str:
    return f"/ads-mcp/{business_key}/{platform}/config"


def _env_manifests() -> dict[str, Any]:
    raw = os.getenv("ADS_MCP_CLIENT_MANIFESTS_JSON")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _raw_manifest(business_key: str) -> dict[str, Any] | None:
    env = _env_manifests()
    if business_key in env and isinstance(env[business_key], dict):
        return env[business_key]

    local = _clients_dir() / f"{business_key}.json"
    if local.is_file():
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    settings = get_settings()
    secret = secrets_mod.get_secret(manifest_secret_id(business_key), settings)
    if isinstance(secret, dict):
        return secret
    return None


def load_client_manifest(business_key: str) -> ClientManifest | None:
    raw = _raw_manifest(business_key)
    if raw is None:
        return None
    raw.setdefault("businessKey", business_key)
    return ClientManifest.model_validate(raw)


def _known_business_keys() -> list[str]:
    keys: set[str] = set(_env_manifests().keys())
    d = _clients_dir()
    if d.is_dir():
        keys.update(p.stem for p in d.glob("*.json"))
    settings = get_settings()
    index = secrets_mod.get_secret(INDEX_SECRET_ID, settings)
    if isinstance(index, list):
        keys.update(str(k) for k in index)
    return sorted(keys)


def list_client_manifests(*, include_inactive: bool = False) -> list[ClientManifest]:
    out: list[ClientManifest] = []
    for key in _known_business_keys():
        manifest = load_client_manifest(key)
        if manifest is None:
            continue
        if not include_inactive and manifest.status != "active":
            continue
        out.append(manifest)
    return out
