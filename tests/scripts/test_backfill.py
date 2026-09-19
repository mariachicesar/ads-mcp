from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# The backfill module is underscore-named for importability; invoked as
# `python scripts/backfill_manifests.py`.
from scripts import backfill_manifests as bf
from shared import manifest as manifest_mod


def test_backfill_enables_platform_when_secret_exists(clients_dir, fake_secrets):
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "google-ads")] = {
        "customer_account_id": "111", "developer_token": "d",
        "client_id": "c", "client_secret": "s", "refresh_token": "r",
    }
    flat = {"acme": {"customer_account_id": "111"}}
    bf.backfill_from_flat(flat, dry_run=False)
    manifest = manifest_mod.load_client_manifest("acme")
    assert manifest.platforms["google-ads"].enabled is True
    assert manifest.platforms["analytics"].enabled is False
    assert "acme" in fake_secrets.store[manifest_mod.INDEX_SECRET_ID]
