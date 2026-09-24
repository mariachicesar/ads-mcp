from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared import secrets as secrets_mod
from shared.config import get_settings
from shared.manifest import (
    KNOWN_PLATFORMS,
    REQUIRED_KEYS_BY_PLATFORM,
    credential_secret_id,
    load_client_manifest,
    upsert_client_manifest,
)
from shared.models import ClientManifest, PlatformConfig

METADATA_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "google-ads": ("customer_account_id", "manager_account_id"),
    "analytics": ("property_id",),
    "search-console": ("site_url",),
    "gbp": ("gbp_account_id", "gbp_location_id"),
    "meta-ads": ("ad_account_id",),
}

CREDENTIAL_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    platform: tuple(
        k for k in REQUIRED_KEYS_BY_PLATFORM[platform]
        if k not in METADATA_KEYS_BY_PLATFORM.get(platform, ())
    )
    for platform in KNOWN_PLATFORMS
}


@dataclass
class PlatformInput:
    platform: str
    enabled: bool
    metadata: dict[str, str] = field(default_factory=dict)
    credentials: dict[str, str] = field(default_factory=dict)


def derive_platform_inputs_from_flat(flat: dict) -> list[PlatformInput]:
    def creds(*extra: str) -> dict[str, str]:
        base = {k: flat[k] for k in ("client_id", "client_secret", "refresh_token") if flat.get(k)}
        for k in extra:
            if flat.get(k):
                base[k] = flat[k]
        return base

    inputs: list[PlatformInput] = []
    inputs.append(PlatformInput(
        "google-ads",
        bool(flat.get("customer_account_id")),
        {k: flat[k] for k in ("customer_account_id", "manager_account_id") if flat.get(k)},
        creds("developer_token"),
    ))
    inputs.append(PlatformInput(
        "analytics",
        bool(flat.get("ga4_property_id")),
        {"property_id": flat["ga4_property_id"]} if flat.get("ga4_property_id") else {},
        creds(),
    ))
    inputs.append(PlatformInput(
        "search-console",
        bool(flat.get("site_url")),
        {"site_url": flat["site_url"]} if flat.get("site_url") else {},
        creds(),
    ))
    inputs.append(PlatformInput(
        "gbp",
        bool(flat.get("gbp_account_id") and flat.get("gbp_location_id")),
        {k: flat[k] for k in ("gbp_account_id", "gbp_location_id") if flat.get(k)},
        creds(),
    ))
    inputs.append(PlatformInput("meta-ads", False, {}, {}))
    return inputs


def build_manifest(
    business_key: str,
    display_name: str,
    *,
    tenant_id: int | None = None,
    industry: str | None = None,
    status: str = "active",
    platform_inputs: list[PlatformInput],
    existing: ClientManifest | None = None,
) -> ClientManifest:
    platforms: dict[str, PlatformConfig] = {}
    by_platform = {pi.platform: pi for pi in platform_inputs}
    for platform in KNOWN_PLATFORMS:
        pi = by_platform.get(platform)
        if pi is None and existing is not None:
            platforms[platform] = existing.platforms.get(platform, PlatformConfig(enabled=False))
        elif pi is None:
            platforms[platform] = PlatformConfig(enabled=False)
        else:
            platforms[platform] = PlatformConfig(enabled=pi.enabled, **pi.metadata)
    return ClientManifest(
        businessKey=business_key,
        displayName=display_name,
        tenantId=tenant_id if tenant_id is not None else (existing.tenantId if existing else None),
        industry=industry if industry is not None else (existing.industry if existing else None),
        status=status,
        platforms=platforms,
        createdAt=existing.createdAt if existing else None,
    )


def apply_onboarding(
    manifest: ClientManifest,
    platform_inputs: list[PlatformInput],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    saved = upsert_client_manifest(manifest, dry_run=dry_run)
    settings = get_settings()
    secrets_written: list[str] = []
    for pi in platform_inputs:
        credentials = {key: value for key, value in pi.credentials.items() if value}
        if not pi.enabled or not credentials:
            continue
        sid = credential_secret_id(manifest.businessKey, pi.platform)
        existing_secret = secrets_mod.get_secret(sid, settings) if not dry_run else None
        merged = dict(existing_secret) if isinstance(existing_secret, dict) else {}
        merged.update(credentials)
        merged.update({k: v for k, v in pi.metadata.items() if v})
        if not dry_run:
            secrets_mod.put_secret(sid, merged, settings)
        secrets_written.append(sid)
    return {
        "manifest": saved.public_dict(),
        "secretsWritten": secrets_written,
        "dryRun": dry_run,
    }


def validate_platform(business_key: str, platform: str) -> tuple[bool, str]:
    try:
        if platform == "google-ads":
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "servers" / "google-ads"))
            from tools.read import list_accounts  # type: ignore
            from shared.models import ToolRequest

            resp = list_accounts(ToolRequest(businessKey=business_key), request_id=None)
            return bool(resp.get("ok")), resp.get("summary") or "ok"
        # Other platforms: presence check only for now.
        from shared.manifest import client_readiness

        report = client_readiness(business_key)
        if report is None:
            return False, "no manifest"
        pr = report.platforms[platform]
        return pr.ready, ("ready" if pr.ready else f"missing: {pr.missingKeys}")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
