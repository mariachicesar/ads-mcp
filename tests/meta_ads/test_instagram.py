from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from shared import approval_store, media_queue
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.fake_s3 import fake_s3  # noqa: F401  (fixture)
from tests.service_import import load_service_module

instagram = load_service_module("meta-ads", "tools.instagram")

CONFIG = {"ad_account_id": "act_123", "access_token": "tok", "app_secret": "sec", "page_id": "999"}
NOW = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)  # 08:00 PST


@pytest.fixture
def ig_env(monkeypatch, fake_s3):
    env = {"ig_user_id": "17841400000000000", "s3": fake_s3}
    monkeypatch.setattr(instagram, "load_meta_ads_config", lambda **kw: dict(CONFIG))
    monkeypatch.setattr(instagram, "get_instagram_user_id", lambda config, *, page_id, tool=None: env["ig_user_id"])
    monkeypatch.setattr(instagram.publish, "_now", lambda: NOW)
    monkeypatch.setattr(approval_store, "_MEMORY_APPROVAL_STORE", approval_store.InMemoryApprovalStore())
    return env


def _req(payload, approval_id=None):
    return ToolRequest(businessKey="el-cuis", approvalId=approval_id, payload=payload)


def _payload(tmp_path, **extra):
    f = tmp_path / "reel.mp4"
    f.write_bytes(b"reel-bytes")
    return {"filePath": str(f), "scheduledTime": "2026-11-12T18:00:00", "caption": "Serenatas en LA 🌹 #mariachi", **extra}


def test_schedule_dry_run_makes_no_s3_writes(ig_env, tmp_path):
    r = instagram.schedule_instagram_reel(_req(_payload(tmp_path)), None)

    assert r["approvalId"]
    assert "up to 30 minutes after" in r["summary"]
    assert ig_env["s3"].objects == {}


def test_schedule_execute_uploads_and_enqueues(ig_env, tmp_path):
    payload = _payload(tmp_path)
    dry = instagram.schedule_instagram_reel(_req(payload), None)
    done = instagram.schedule_instagram_reel(_req(payload, dry["approvalId"]), None)

    keys = sorted(ig_env["s3"].objects)
    assert len(keys) == 2 and keys[0].startswith("queue/pending/20261113T0200Z-") and keys[1].startswith("videos/el-cuis/")
    entry = json.loads(ig_env["s3"].objects[keys[0]])
    assert entry["platform"] == "instagram" and entry["scheduledAt"] == "2026-11-13T02:00:00Z"
    assert entry["caption"] == "Serenatas en LA 🌹 #mariachi" and entry["options"] == {"shareToFeed": True}
    assert entry["igUserId"] == "17841400000000000"
    assert done["data"]["result"]["entryId"] == entry["id"]


def test_schedule_rejects_changed_file(ig_env, tmp_path):
    payload = _payload(tmp_path)
    dry = instagram.schedule_instagram_reel(_req(payload), None)
    (tmp_path / "reel.mp4").write_bytes(b"other")

    with pytest.raises(AdsMcpError):
        instagram.schedule_instagram_reel(_req(payload, dry["approvalId"]), None)
    assert ig_env["s3"].objects == {}


def test_schedule_blocked_when_instagram_not_linked(ig_env, tmp_path):
    ig_env["ig_user_id"] = None
    r = instagram.schedule_instagram_reel(_req(_payload(tmp_path)), None)

    assert "approvalId" not in r
    assert any(c["rule"] == "instagram-linked" and not c["passed"] for c in r["ruleChecks"])


def test_schedule_blocked_outside_window(ig_env, tmp_path):
    r = instagram.schedule_instagram_reel(_req(_payload(tmp_path, scheduledTime="2026-11-10T08:05:00")), None)
    assert "approvalId" not in r


@pytest.mark.parametrize("caption", ["x" * 2201, " ".join(f"#t{i}" for i in range(31)), "   "])
def test_schedule_rejects_bad_captions(ig_env, tmp_path, caption):
    with pytest.raises(AdsMcpError) as exc:
        instagram.schedule_instagram_reel(_req(_payload(tmp_path, caption=caption)), None)
    assert exc.value.error_code == "REQUEST_INVALID"


def test_schedule_rejects_file_url(ig_env):
    with pytest.raises(AdsMcpError) as exc:
        instagram.schedule_instagram_reel(_req({"fileUrl": "https://cdn.example/a.mp4",
                                                "scheduledTime": "2026-11-12T18:00:00", "caption": "hola"}), None)
    assert exc.value.error_code == "REQUEST_INVALID"


def _queued(state="pending", **extra):
    key = media_queue.enqueue({"platform": "instagram", "businessKey": "el-cuis", "scheduledAt": "2026-11-13T02:00:00Z",
                               "videoKey": "videos/el-cuis/x.mp4", "videoSha256": "x", "caption": "hola",
                               "options": {"shareToFeed": True}, **extra})
    if state != "pending":
        key = media_queue.move(key, state)
    return key, json.loads(media_queue._s3_client().objects[key])["id"]


def test_cancel_moves_pending_to_cancelled(ig_env):
    key, entry_id = _queued()
    dry = instagram.cancel_scheduled_social(_req({"entryId": entry_id}), None)
    instagram.cancel_scheduled_social(_req({"entryId": entry_id}, dry["approvalId"]), None)

    assert key not in ig_env["s3"].objects
    assert any(k.startswith("queue/cancelled/") for k in ig_env["s3"].objects)


def test_cancel_only_pending(ig_env):
    _, entry_id = _queued(state="processing")
    r = instagram.cancel_scheduled_social(_req({"entryId": entry_id}), None)

    assert "approvalId" not in r
    assert any(c["rule"] == "entry-is-pending" and not c["passed"] for c in r["ruleChecks"])


def test_cancel_unknown_entry(ig_env):
    with pytest.raises(AdsMcpError) as exc:
        instagram.cancel_scheduled_social(_req({"entryId": "nope"}), None)
    assert exc.value.error_code == "REQUEST_INVALID"


def test_list_defaults_hide_done_and_cancelled(ig_env):
    _queued()
    _queued(state="done")
    _queued(state="cancelled")
    _queued(state="failed", lastError="boom")

    r = instagram.list_scheduled_social(_req({}), None)

    assert r["mode"] == "read"
    assert sorted(e["state"] for e in r["data"]["entries"]) == ["failed", "pending"]
    assert r["data"]["entries"][0]["scheduledPacific"] == "2026-11-12T18:00:00-08:00"
    assert "_key" not in r["data"]["entries"][0]


def test_routes_and_tools_registered():
    main = load_service_module("meta-ads", "main")
    paths = {r.path for r in main.app.routes}
    assert {"/tools/schedule_instagram_reel", "/tools/list_scheduled_social", "/tools/cancel_scheduled_social"} <= paths
    server = load_service_module("meta-ads", "mcp_server")
    for name in ("meta_ads_schedule_instagram_reel", "meta_ads_list_scheduled_social", "meta_ads_cancel_scheduled_social"):
        assert hasattr(server, name)
