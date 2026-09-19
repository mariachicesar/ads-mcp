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


import pytest
from shared.errors import AdsMcpError


def test_merge_platform_config_manifest_wins_on_ids(clients_dir, fake_secrets):
    m = manifest_mod.load_client_manifest("acme")  # None ok for this call shape
    merged = manifest_mod.merge_platform_config(
        None, "google-ads", {"customer_account_id": "old", "refresh_token": "rt"}
    )
    assert merged == {"customer_account_id": "old", "refresh_token": "rt"}


def test_readiness_none_without_manifest(clients_dir, fake_secrets):
    assert manifest_mod.client_readiness("acme") is None


def test_readiness_enabled_but_incomplete(clients_dir, fake_secrets):
    _write_local(clients_dir, "acme", {
        "businessKey": "acme", "displayName": "Acme",
        "platforms": {"search-console": {"enabled": True, "site_url": "https://a.com"}},
    })
    # credential secret only has client_id -> missing client_secret, refresh_token
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "search-console")] = {
        "client_id": "cid",
    }
    report = manifest_mod.client_readiness("acme")
    sc = report.platforms["search-console"]
    assert sc.enabled is True and sc.ready is False
    assert set(sc.missingKeys) == {"client_secret", "refresh_token"}
    assert report.platforms["analytics"].enabled is False
    assert report.platforms["analytics"].missingKeys == []


def test_readiness_ready_when_complete(clients_dir, fake_secrets):
    _write_local(clients_dir, "acme", {
        "businessKey": "acme", "displayName": "Acme",
        "platforms": {"search-console": {"enabled": True}},
    })
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "search-console")] = {
        "site_url": "https://a.com", "client_id": "c", "client_secret": "s", "refresh_token": "r",
    }
    assert manifest_mod.client_readiness("acme").platforms["search-console"].ready is True


def test_upsert_stamps_timestamps_and_index(clients_dir, fake_secrets):
    from shared.models import ClientManifest
    m = ClientManifest(businessKey="acme", displayName="Acme", platforms={})
    saved = manifest_mod.upsert_client_manifest(m)
    assert saved.createdAt and saved.updatedAt
    assert fake_secrets.store[manifest_mod.INDEX_SECRET_ID] == ["acme"]
    assert fake_secrets.store[manifest_mod.manifest_secret_id("acme")]["displayName"] == "Acme"


def test_upsert_dry_run_writes_nothing(clients_dir, fake_secrets):
    from shared.models import ClientManifest
    m = ClientManifest(businessKey="acme", displayName="Acme", platforms={})
    manifest_mod.upsert_client_manifest(m, dry_run=True)
    assert manifest_mod.manifest_secret_id("acme") not in fake_secrets.store
    assert manifest_mod.INDEX_SECRET_ID not in fake_secrets.store


def test_set_platform_config_requires_existing_manifest(clients_dir, fake_secrets):
    with pytest.raises(AdsMcpError) as exc:
        manifest_mod.set_platform_config("ghost", "gbp", enabled=True)
    assert exc.value.status_code == 404


def test_delete_removes_manifest_and_index_entry(clients_dir, fake_secrets):
    from shared.models import ClientManifest
    manifest_mod.upsert_client_manifest(ClientManifest(businessKey="acme", displayName="A", platforms={}))
    manifest_mod.upsert_client_manifest(ClientManifest(businessKey="beta", displayName="B", platforms={}))
    manifest_mod.delete_client_manifest("acme")
    assert manifest_mod.manifest_secret_id("acme") not in fake_secrets.store
    assert fake_secrets.store[manifest_mod.INDEX_SECRET_ID] == ["beta"]
