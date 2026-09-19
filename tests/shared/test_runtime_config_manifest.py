from __future__ import annotations

import json

import pytest

from shared import manifest as manifest_mod
from shared.errors import AdsMcpError
from shared.runtime_config import load_platform_runtime_config


def _manifest(clients_dir, key, platforms):
    (clients_dir / f"{key}.json").write_text(json.dumps({
        "businessKey": key, "displayName": key.title().replace("-", " "),
        "platforms": platforms,
    }))


def test_no_manifest_keeps_legacy_behavior(clients_dir, fake_secrets, monkeypatch):
    monkeypatch.setenv(
        "ADS_MCP_ANALYTICS_CONFIGS_JSON",
        json.dumps({"acme": {"property_id": "1", "client_id": "c", "client_secret": "s", "refresh_token": "r"}}),
    )
    cfg = load_platform_runtime_config(
        platform="analytics", business_key="acme",
        required_keys=("property_id", "client_id", "client_secret", "refresh_token"),
    )
    assert cfg["property_id"] == "1"


def test_no_manifest_no_config_is_400(clients_dir, fake_secrets):
    with pytest.raises(AdsMcpError) as exc:
        load_platform_runtime_config(platform="gbp", business_key="acme")
    assert exc.value.status_code == 400
    assert exc.value.error_code == "REQUEST_INVALID"


def test_platform_disabled_is_404(clients_dir, fake_secrets):
    _manifest(clients_dir, "acme", {"google-ads": {"enabled": True}})
    with pytest.raises(AdsMcpError) as exc:
        load_platform_runtime_config(platform="analytics", business_key="acme")
    assert exc.value.status_code == 404
    assert exc.value.error_code == "PLATFORM_NOT_CONFIGURED"
    assert "not set up for analytics" in exc.value.message


def test_platform_enabled_incomplete_is_409(clients_dir, fake_secrets):
    _manifest(clients_dir, "acme", {"search-console": {"enabled": True, "site_url": "https://a.com"}})
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "search-console")] = {"client_id": "c"}
    with pytest.raises(AdsMcpError) as exc:
        load_platform_runtime_config(
            platform="search-console", business_key="acme",
            required_keys=("site_url", "client_id", "client_secret", "refresh_token"),
        )
    assert exc.value.status_code == 409
    assert exc.value.error_code == "PLATFORM_CONFIG_INCOMPLETE"
    assert set(exc.value.details["missingKeys"]) == {"client_secret", "refresh_token"}


def test_platform_enabled_complete_merges_manifest_metadata(clients_dir, fake_secrets):
    _manifest(clients_dir, "acme", {"google-ads": {"enabled": True, "customer_account_id": "111", "manager_account_id": "999"}})
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "google-ads")] = {
        "customer_account_id": "OLD", "developer_token": "d",
        "client_id": "c", "client_secret": "s", "refresh_token": "r",
    }
    cfg = load_platform_runtime_config(
        platform="google-ads", business_key="acme",
        required_keys=("customer_account_id", "developer_token", "client_id", "client_secret", "refresh_token"),
    )
    assert cfg["customer_account_id"] == "111"  # manifest wins
    assert cfg["manager_account_id"] == "999"
    assert cfg["refresh_token"] == "r"
