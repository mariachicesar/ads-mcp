from __future__ import annotations

from shared.models import ClientManifest, PlatformConfig, KNOWN_PLATFORMS


def test_manifest_fills_missing_platform_keys():
    m = ClientManifest(
        businessKey="acme",
        displayName="Acme",
        platforms={"google-ads": PlatformConfig(enabled=True, customer_account_id="123")},
    )
    assert set(m.platforms) == set(KNOWN_PLATFORMS)
    assert m.platforms["analytics"].enabled is False
    assert m.platforms["google-ads"].metadata() == {"customer_account_id": "123"}


def test_known_platforms_order():
    assert KNOWN_PLATFORMS == (
        "google-ads", "analytics", "search-console", "gbp", "meta-ads",
    )
