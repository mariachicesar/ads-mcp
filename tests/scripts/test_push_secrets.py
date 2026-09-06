from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import _onboard_core as core
from shared import manifest as manifest_mod


def test_no_google_ads_secret_when_customer_id_absent(clients_dir, fake_secrets):
    flat = {"acme": {"site_url": "https://a.com", "client_id": "c",
                     "client_secret": "s", "refresh_token": "r"}}
    for key, obj in flat.items():
        inputs = core.derive_platform_inputs_from_flat(obj)
        manifest = core.build_manifest(key, key.title(), platform_inputs=inputs)
        core.apply_onboarding(manifest, inputs)
    assert manifest_mod.credential_secret_id("acme", "google-ads") not in fake_secrets.store
    assert manifest_mod.credential_secret_id("acme", "search-console") in fake_secrets.store
    assert fake_secrets.store[manifest_mod.manifest_secret_id("acme")]["platforms"]["google-ads"]["enabled"] is False
