"""In-memory stand-in for the TikTok endpoints shared.tiktok_client calls (via `requests`)."""

from __future__ import annotations

import pytest

UPLOAD_URL = "https://open-upload.tiktokapis.com/upload/?upload_id=1&upload_token=secret"


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code, self._body = status_code, body or {}

    def json(self):
        return self._body


class FakeTikTok:
    def __init__(self):
        self.calls: list[dict] = []
        self.token_body = {"access_token": "act.new", "refresh_token": "rft.new", "open_id": "oid",
                           "expires_in": 86400, "refresh_expires_in": 31536000, "scope": "user.info.basic,video.upload"}
        self.init_body = {"data": {"publish_id": "v_inbox_file~v2.123", "upload_url": UPLOAD_URL},
                          "error": {"code": "ok", "message": "", "log_id": "l1"}}
        self.statuses = ["SEND_TO_USER_INBOX"]
        self.fail_reason = None

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "data": data, "json": json, "headers": headers})
        if url.endswith("/oauth/token/"):
            return FakeResponse(200, self.token_body)
        if url.endswith("/inbox/video/init/"):
            return FakeResponse(200, self.init_body)
        if url.endswith("/status/fetch/"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            body = {"status": status}
            if self.fail_reason:
                body["fail_reason"] = self.fail_reason
            return FakeResponse(200, {"data": body, "error": {"code": "ok", "message": ""}})
        raise AssertionError(f"unexpected POST {url}")

    def put(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"method": "PUT", "url": url, "len": len(data), "headers": headers})
        end, total = headers["Content-Range"].removeprefix("bytes ").split("/")
        last = int(end.split("-")[1]) == int(total) - 1
        return FakeResponse(201 if last else 206)


@pytest.fixture
def fake_tt(monkeypatch):
    from shared import tiktok_client

    fake = FakeTikTok()
    monkeypatch.setattr(tiktok_client, "requests", fake)
    return fake
