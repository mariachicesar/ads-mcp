from __future__ import annotations

import json

from shared import youtube_client

CONFIG = {"client_id": "id", "client_secret": "secret", "refresh_token": "rt"}


class _Api:
    def __init__(self, items):
        self.items, self.calls = items, []

    def videos(self):
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return type("Call", (), {"execute": lambda _self: {"items": self.items}})()


def test_youtube_status_returns_status_fields(monkeypatch):
    monkeypatch.setenv("ADS_MCP_YOUTUBE_CONFIGS_JSON", json.dumps({"el-cuis": CONFIG}))
    api = _Api([{"id": "v1", "status": {"privacyStatus": "private", "uploadStatus": "processed",
                                        "publishAt": "2026-11-20T03:00:00Z", "license": "youtube"}}])
    seen = []
    monkeypatch.setattr(youtube_client, "data_client", lambda config: seen.append(config) or api)

    assert youtube_client.youtube_status("el-cuis", "v1") == {
        "privacyStatus": "private", "uploadStatus": "processed", "publishAt": "2026-11-20T03:00:00Z"}
    assert api.calls == [{"part": "status", "id": "v1"}] and seen[0]["refresh_token"] == "rt"


def test_youtube_status_none_when_missing(monkeypatch):
    monkeypatch.setenv("ADS_MCP_YOUTUBE_CONFIGS_JSON", json.dumps({"el-cuis": CONFIG}))
    monkeypatch.setattr(youtube_client, "data_client", lambda config: _Api([]))

    assert youtube_client.youtube_status("el-cuis", "gone") is None
