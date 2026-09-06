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


import json

from shared import manifest as manifest_mod
from shared.config import get_settings


def _write_local(clients_dir, key, data):
    (clients_dir / f"{key}.json").write_text(json.dumps(data))


def test_load_prefers_env_over_file_over_secret(clients_dir, fake_secrets, monkeypatch):
    _write_local(clients_dir, "acme", {"businessKey": "acme", "displayName": "File"})
    fake_secrets.store[manifest_mod.manifest_secret_id("acme")] = {
        "businessKey": "acme", "displayName": "Secret",
    }
    monkeypatch.setenv(
        "ADS_MCP_CLIENT_MANIFESTS_JSON",
        json.dumps({"acme": {"businessKey": "acme", "displayName": "Env"}}),
    )
    assert manifest_mod.load_client_manifest("acme").displayName == "Env"


def test_load_falls_back_to_file_then_secret(clients_dir, fake_secrets):
    fake_secrets.store[manifest_mod.manifest_secret_id("acme")] = {
        "businessKey": "acme", "displayName": "Secret",
    }
    assert manifest_mod.load_client_manifest("acme").displayName == "Secret"
    _write_local(clients_dir, "acme", {"businessKey": "acme", "displayName": "File"})
    assert manifest_mod.load_client_manifest("acme").displayName == "File"


def test_load_missing_returns_none(clients_dir, fake_secrets):
    assert manifest_mod.load_client_manifest("nope") is None


def test_list_unions_and_dedupes(clients_dir, fake_secrets):
    _write_local(clients_dir, "acme", {"businessKey": "acme", "displayName": "Acme"})
    fake_secrets.store[manifest_mod.INDEX_SECRET_ID] = ["acme", "beta"]
    fake_secrets.store[manifest_mod.manifest_secret_id("beta")] = {
        "businessKey": "beta", "displayName": "Beta",
    }
    keys = [m.businessKey for m in manifest_mod.list_client_manifests()]
    assert keys == ["acme", "beta"]


def test_list_excludes_inactive_by_default(clients_dir, fake_secrets):
    _write_local(clients_dir, "acme", {"businessKey": "acme", "displayName": "A", "status": "archived"})
    assert manifest_mod.list_client_manifests() == []
    assert len(manifest_mod.list_client_manifests(include_inactive=True)) == 1
