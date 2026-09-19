from __future__ import annotations

import json

from shared.config import get_settings
from shared import secrets as secrets_mod


def test_put_secret_json_encodes_dicts(fake_secrets):
    settings = get_settings()
    secrets_mod.put_secret("/ads-mcp/acme/manifest", {"businessKey": "acme"}, settings)
    assert fake_secrets.store["/ads-mcp/acme/manifest"] == {"businessKey": "acme"}


def test_get_after_put_roundtrips(fake_secrets):
    settings = get_settings()
    secrets_mod.put_secret("/ads-mcp/_index/clients", ["acme"], settings)
    assert secrets_mod.get_secret("/ads-mcp/_index/clients", settings) == ["acme"]


def test_delete_secret_is_idempotent(fake_secrets):
    settings = get_settings()
    secrets_mod.delete_secret("/ads-mcp/missing/manifest", settings)  # no raise
    secrets_mod.put_secret("/ads-mcp/acme/manifest", {"x": 1}, settings)
    secrets_mod.delete_secret("/ads-mcp/acme/manifest", settings)
    assert "/ads-mcp/acme/manifest" not in fake_secrets.store
