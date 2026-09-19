from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import _onboard_core as core
from shared import manifest as manifest_mod


def test_derive_from_flat_only_enables_present_platforms():
    flat = {
        "customer_account_id": "111", "manager_account_id": "999",
        "developer_token": "d", "client_id": "c", "client_secret": "s", "refresh_token": "r",
        "site_url": "https://a.com",
    }
    inputs = {pi.platform: pi for pi in core.derive_platform_inputs_from_flat(flat)}
    assert inputs["google-ads"].enabled is True
    assert inputs["search-console"].enabled is True
    assert inputs["analytics"].enabled is False
    assert inputs["gbp"].enabled is False
    assert inputs["meta-ads"].enabled is False
    assert inputs["google-ads"].metadata["customer_account_id"] == "111"
    assert inputs["google-ads"].credentials["refresh_token"] == "r"


def test_apply_onboarding_writes_only_enabled_secrets(clients_dir, fake_secrets):
    inputs = [
        core.PlatformInput("google-ads", True,
                           {"customer_account_id": "111"},
                           {"developer_token": "d", "client_id": "c", "client_secret": "s", "refresh_token": "r"}),
        core.PlatformInput("analytics", False, {}, {}),
    ]
    manifest = core.build_manifest("acme", "Acme", platform_inputs=inputs)
    result = core.apply_onboarding(manifest, inputs)
    assert manifest_mod.credential_secret_id("acme", "google-ads") in fake_secrets.store
    assert manifest_mod.credential_secret_id("acme", "analytics") not in fake_secrets.store
    assert fake_secrets.store[manifest_mod.INDEX_SECRET_ID] == ["acme"]
    stored = fake_secrets.store[manifest_mod.manifest_secret_id("acme")]
    assert stored["platforms"]["google-ads"]["enabled"] is True
    assert "customer_account_id" not in fake_secrets.store[manifest_mod.credential_secret_id("acme", "google-ads")] \
        or fake_secrets.store[manifest_mod.credential_secret_id("acme", "google-ads")].get("developer_token") == "d"


def test_apply_onboarding_dry_run_writes_nothing(clients_dir, fake_secrets):
    inputs = [core.PlatformInput("gbp", True, {"gbp_location_id": "loc"},
                                 {"client_id": "c", "client_secret": "s", "refresh_token": "r"})]
    manifest = core.build_manifest("acme", "Acme", platform_inputs=inputs)
    result = core.apply_onboarding(manifest, inputs, dry_run=True)
    assert result["dryRun"] is True
    assert fake_secrets.store == {}


def test_build_manifest_preserves_existing_created_at(clients_dir, fake_secrets):
    from shared.models import ClientManifest
    existing = ClientManifest(businessKey="acme", displayName="Old", createdAt="2020-01-01T00:00:00Z", platforms={})
    m = core.build_manifest("acme", "Acme", platform_inputs=[], existing=existing)
    assert m.createdAt == "2020-01-01T00:00:00Z"
    assert m.displayName == "Acme"
