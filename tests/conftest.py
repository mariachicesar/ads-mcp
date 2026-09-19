from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


class _FakeSecrets:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def get_secret(self, secret_id: str, settings) -> Any:  # noqa: ANN001
        return self.store.get(secret_id)

    def put_secret(self, secret_id: str, value: Any, settings) -> None:  # noqa: ANN001
        self.store[secret_id] = value

    def delete_secret(self, secret_id: str, settings) -> None:  # noqa: ANN001
        self.store.pop(secret_id, None)


@pytest.fixture(autouse=True)
def fake_secrets(monkeypatch) -> _FakeSecrets:
    fake = _FakeSecrets()
    monkeypatch.setattr("shared.secrets.get_secret", fake.get_secret)
    monkeypatch.setattr("shared.secrets.put_secret", fake.put_secret)
    monkeypatch.setattr("shared.secrets.delete_secret", fake.delete_secret)
    # Ensure a region is set so Settings.aws_region is truthy.
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    return fake


@pytest.fixture(autouse=True)
def clients_dir(monkeypatch, tmp_path) -> Path:
    d = tmp_path / "clients"
    d.mkdir()
    monkeypatch.setenv("ADS_MCP_CLIENTS_DIR", str(d))
    monkeypatch.delenv("ADS_MCP_CLIENT_MANIFESTS_JSON", raising=False)
    return d


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from shared.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
