from __future__ import annotations

import hashlib
import hmac

import pytest

from shared import meta_ads_client
from shared.errors import AdsMcpError

CONFIG = {"ad_account_id": "act_123", "access_token": "tok", "app_secret": "sec", "page_id": "999", "api_version": "v23.0"}


class FakeRequests:
    """Records every request; returns queued JSON bodies (default {"id": "x"})."""

    def __init__(self):
        self.calls: list[dict] = []
        self.bodies: list[tuple[int, dict]] = []

    def _respond(self):
        status, body = self.bodies.pop(0) if self.bodies else (200, {"id": "x"})

        class Response:
            status_code = status
            text = ""

            def json(self):
                return body

        return Response()

    def request(self, method, url, *, params, data, headers, timeout, files=None):
        self.calls.append({"method": method, "url": url, "params": params, "data": data, "headers": headers, "files": files})
        return self._respond()

    def post(self, url, *, headers, data=None, params=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "data": data, "params": params, "files": None})
        return self._respond()


@pytest.fixture
def fake_requests(monkeypatch):
    fake = FakeRequests()
    monkeypatch.setattr(meta_ads_client, "import_module", lambda name: fake)
    return fake


def test_token_override_changes_header_and_proof(fake_requests):
    meta_ads_client._graph_request(CONFIG, "GET", "999", access_token="page-tok")
    call = fake_requests.calls[-1]

    assert call["headers"]["Authorization"] == "Bearer page-tok"
    assert call["params"]["appsecret_proof"] == hmac.new(b"sec", b"page-tok", hashlib.sha256).hexdigest()


def test_post_video_uses_graph_video_host_and_multipart(fake_requests, tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")

    meta_ads_client.post_video(CONFIG, path="act_123/advideos", fields={"name": "V"}, file_path=str(f), file_url=None)
    call = fake_requests.calls[-1]

    assert call["url"] == "https://graph-video.facebook.com/v23.0/act_123/advideos"
    assert "source" in call["files"]
    assert call["data"]["name"] == "V"


def test_post_video_with_url_sends_file_url_and_no_files(fake_requests):
    meta_ads_client.post_video(CONFIG, path="act_123/advideos", fields={"name": "V"}, file_path=None,
                               file_url="https://cdn.example/v.mp4")
    call = fake_requests.calls[-1]

    assert call["files"] is None
    assert call["data"]["file_url"] == "https://cdn.example/v.mp4"


def test_upload_reel_bytes_headers(fake_requests, tmp_path):
    f = tmp_path / "r.mp4"
    f.write_bytes(b"abc")
    fake_requests.bodies.append((200, {"success": True}))

    meta_ads_client.upload_reel_bytes(CONFIG, video_id="vid1", access_token="page-tok", file_path=str(f), file_url=None)
    call = fake_requests.calls[-1]

    assert call["url"] == "https://rupload.facebook.com/video-upload/v23.0/vid1"
    assert call["headers"]["Authorization"] == "OAuth page-tok"
    assert call["headers"]["offset"] == "0"
    assert call["headers"]["file_size"] == "3"


def test_upload_reel_bytes_failure_carries_video_id(fake_requests):
    fake_requests.bodies.append((400, {"error": {"message": "bad", "code": 100}}))

    with pytest.raises(AdsMcpError) as exc:
        meta_ads_client.upload_reel_bytes(CONFIG, video_id="vid1", access_token="page-tok", file_path=None,
                                          file_url="https://cdn.example/r.mp4")

    assert exc.value.details["videoId"] == "vid1"


def test_get_page_access_token_returns_token(fake_requests):
    fake_requests.bodies.append((200, {"id": "999", "access_token": "page-tok"}))

    assert meta_ads_client.get_page_access_token(CONFIG, page_id="999") == "page-tok"
    assert fake_requests.calls[-1]["params"]["fields"] == "access_token"


def test_get_page_access_token_missing_permission_message(fake_requests):
    fake_requests.bodies.append((200, {"id": "999"}))

    with pytest.raises(AdsMcpError) as exc:
        meta_ads_client.get_page_access_token(CONFIG, page_id="999")

    assert exc.value.error_code == "UPSTREAM_ERROR"
    assert "pages_manage_posts" in exc.value.message
