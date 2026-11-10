from __future__ import annotations

import hashlib

import pytest

from shared import approval_store
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.service_import import load_service_module

publish = load_service_module("meta-ads", "tools.publish")

CONFIG = {"ad_account_id": "act_123", "access_token": "tok", "app_secret": "sec", "page_id": "999"}


@pytest.fixture
def publish_env(monkeypatch):
    """Stub every Graph call the publish tools make and record them."""
    env: dict[str, list] = {"posts": [], "calls": [], "tokens": []}

    def post_video(config, *, path, fields, file_path, file_url, access_token=None, tool=None):
        env["posts"].append((path, dict(fields)))
        env["tokens"].append(access_token)
        return {"id": "new-video"}

    def get_page_access_token(config, *, page_id, tool=None):
        env["calls"].append(f"token:{page_id}")
        return "page-tok"

    monkeypatch.setattr(publish, "load_meta_ads_config", lambda **kwargs: dict(CONFIG))
    monkeypatch.setattr(publish, "post_video", post_video)
    monkeypatch.setattr(publish, "get_page_access_token", get_page_access_token)
    monkeypatch.setattr(approval_store, "_MEMORY_APPROVAL_STORE", approval_store.InMemoryApprovalStore())
    return env


def _req(payload, approval_id=None):
    return ToolRequest(businessKey="el-cuis", approvalId=approval_id, payload=payload)


def _video(tmp_path, name="a.mp4", data=b"abc"):
    f = tmp_path / name
    f.write_bytes(data)
    return f


# ── upload_ad_video ──────────────────────────────────────────────────────────


def test_upload_ad_video_dry_run_binds_sha(publish_env, tmp_path):
    f = _video(tmp_path)
    r = publish.upload_ad_video(_req({"filePath": str(f), "name": "V1"}), None)

    assert r["approvalId"]
    assert r["data"]["file"]["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert r["data"]["graphRequest"]["path"] == "act_123/advideos"
    assert publish_env["posts"] == []


def test_upload_ad_video_executes_once(publish_env, tmp_path):
    payload = {"filePath": str(_video(tmp_path)), "name": "V1"}
    dry = publish.upload_ad_video(_req(payload), None)
    done = publish.upload_ad_video(_req(payload, dry["approvalId"]), None)

    assert done["executed"] is True
    assert publish_env["posts"] == [("act_123/advideos", {"name": "V1"})]
    assert done["data"]["result"] == {"videoId": "new-video"}
    with pytest.raises(AdsMcpError):
        publish.upload_ad_video(_req(payload, dry["approvalId"]), None)


def test_upload_ad_video_rejects_changed_file(publish_env, tmp_path):
    f = _video(tmp_path)
    payload = {"filePath": str(f), "name": "V1"}
    dry = publish.upload_ad_video(_req(payload), None)
    f.write_bytes(b"abcd")

    with pytest.raises(AdsMcpError):
        publish.upload_ad_video(_req(payload, dry["approvalId"]), None)
    assert publish_env["posts"] == []


def test_upload_ad_video_by_url(publish_env):
    r = publish.upload_ad_video(_req({"fileUrl": "https://cdn.example/v.mp4", "name": "V"}), None)

    assert r["approvalId"]
    assert r["data"]["file"] == {"url": "https://cdn.example/v.mp4"}


@pytest.mark.parametrize("payload", [
    {"name": "V"},                                                    # no source
    {"name": "V", "filePath": "x.mp4", "fileUrl": "https://a/b.mp4"}, # both
    {"name": "V", "fileUrl": "http://a/b.mp4"},                       # not https
    {"name": "V", "filePath": "missing.mp4"},                         # missing file
])
def test_upload_ad_video_rejects_bad_sources(publish_env, payload):
    with pytest.raises(AdsMcpError) as exc:
        publish.upload_ad_video(_req(payload), None)
    assert exc.value.error_code == "REQUEST_INVALID"


def test_video_source_rejects_wrong_extension_and_oversize(tmp_path, monkeypatch):
    with pytest.raises(AdsMcpError):
        publish._video_source({"filePath": str(_video(tmp_path, "a.avi"))}, "t")

    monkeypatch.setattr(publish, "MAX_VIDEO_BYTES", 2)
    with pytest.raises(AdsMcpError):
        publish._video_source({"filePath": str(_video(tmp_path, "b.mp4"))}, "t")
