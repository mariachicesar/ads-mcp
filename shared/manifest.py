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
    "meta-ads": ("ad_account_id", "access_token", "app_id", "app_secret", "page_id"),
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


from datetime import UTC, datetime

from shared.errors import AdsMcpError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def merge_platform_config(
    manifest: ClientManifest | None,
    platform: str,
    secret_config: dict | None,
) -> dict | None:
    meta: dict[str, Any] = {}
    if manifest is not None:
        pc = manifest.platforms.get(platform)
        if pc is not None:
            meta = pc.metadata()
    if secret_config is None and not meta:
        return None
    return {**(secret_config or {}), **meta}


def _credential_secret(business_key: str, platform: str) -> dict | None:
    settings = get_settings()
    value = secrets_mod.get_secret(credential_secret_id(business_key, platform), settings)
    return value if isinstance(value, dict) else None


def client_readiness(business_key: str) -> ReadinessReport | None:
    manifest = load_client_manifest(business_key)
    if manifest is None:
        return None
    platforms: dict[str, PlatformReadiness] = {}
    for platform in KNOWN_PLATFORMS:
        pc = manifest.platforms.get(platform)
        enabled = bool(pc and pc.enabled)
        if not enabled:
            platforms[platform] = PlatformReadiness(enabled=False, ready=False, missingKeys=[])
            continue
        merged = merge_platform_config(manifest, platform, _credential_secret(business_key, platform)) or {}
        missing = [k for k in REQUIRED_KEYS_BY_PLATFORM[platform] if not merged.get(k)]
        platforms[platform] = PlatformReadiness(
            enabled=True, ready=not missing, missingKeys=missing
        )
    return ReadinessReport(
        businessKey=manifest.businessKey,
        displayName=manifest.displayName,
        status=manifest.status,
        platforms=platforms,
    )


def _add_to_index(business_key: str) -> None:
    settings = get_settings()
    current = secrets_mod.get_secret(INDEX_SECRET_ID, settings)
    keys = set(current) if isinstance(current, list) else set()
    if business_key not in keys:
        keys.add(business_key)
        secrets_mod.put_secret(INDEX_SECRET_ID, sorted(keys), settings)


def _remove_from_index(business_key: str) -> None:
    settings = get_settings()
    current = secrets_mod.get_secret(INDEX_SECRET_ID, settings)
    if not isinstance(current, list):
        return
    remaining = sorted(k for k in current if k != business_key)
    secrets_mod.put_secret(INDEX_SECRET_ID, remaining, settings)


def upsert_client_manifest(
    manifest: ClientManifest, *, dry_run: bool = False
) -> ClientManifest:
    existing = load_client_manifest(manifest.businessKey)
    now = _now_iso()
    manifest.createdAt = (existing.createdAt if existing and existing.createdAt else manifest.createdAt) or now
    manifest.updatedAt = now
    if dry_run:
        return manifest
    settings = get_settings()
    secrets_mod.put_secret(manifest_secret_id(manifest.businessKey), manifest.model_dump(), settings)
    _add_to_index(manifest.businessKey)
    return manifest


def set_platform_config(
    business_key: str,
    platform: str,
    *,
    enabled: bool,
    metadata: dict | None = None,
    dry_run: bool = False,
) -> ClientManifest:
    if platform not in KNOWN_PLATFORMS:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"Unknown platform '{platform}'.",
            details={"knownPlatforms": list(KNOWN_PLATFORMS)},
        )
    manifest = load_client_manifest(business_key)
    if manifest is None:
        raise AdsMcpError(
            status_code=404,
            error_code="PLATFORM_NOT_CONFIGURED",
            message=f"No client manifest for businessKey '{business_key}'. Run onboard-client.py add first.",
            details={"businessKey": business_key},
        )
    from shared.models import PlatformConfig

    manifest.platforms[platform] = PlatformConfig(enabled=enabled, **(metadata or {}))
    return upsert_client_manifest(manifest, dry_run=dry_run)


def delete_client_manifest(business_key: str, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    settings = get_settings()
    secrets_mod.delete_secret(manifest_secret_id(business_key), settings)
    _remove_from_index(business_key)
