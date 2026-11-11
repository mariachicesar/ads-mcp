from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from shared import approval_store, media_queue, social_ledger
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.fake_s3 import PreconditionFailed, fake_s3  # noqa: F401  (fixture)
from tests.service_import import load_service_module
from tests.social_fixtures import GOOD

instagram = load_service_module("meta-ads", "tools.instagram")

CONFIG = {"ad_account_id": "act_123", "access_token": "tok", "app_secret": "sec", "page_id": "999"}
NOW = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)  # 08:00 PST


@pytest.fixture
def ig_env(monkeypatch, fake_s3):
    env = {"ig_user_id": "17841400000000000", "s3": fake_s3}
    monkeypatch.setattr(instagram, "load_meta_ads_config", lambda **kw: dict(CONFIG))
    monkeypatch.setattr(instagram, "get_instagram_user_id", lambda config, *, page_id, tool=None: env["ig_user_id"])
    monkeypatch.setattr(instagram, "check_instagram_publish_access",
                        lambda config, *, ig_user_id, tool=None: (True, f"Posts to Instagram @mariachielcuis ({ig_user_id}).", "mariachielcuis"))
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


def test_schedule_blocked_when_token_cannot_publish(ig_env, tmp_path, monkeypatch):
    monkeypatch.setattr(instagram, "check_instagram_publish_access",
                        lambda config, *, ig_user_id, tool=None: (False, "token lacks instagram_content_publish", None))
    r = instagram.schedule_instagram_reel(_req(_payload(tmp_path)), None)

    assert "approvalId" not in r
    check = next(c for c in r["ruleChecks"] if c["rule"] == "instagram-linked")
    assert not check["passed"] and "instagram_content_publish" in check["message"]


def test_schedule_rejects_video_over_instagram_limit(ig_env, tmp_path, monkeypatch):
    monkeypatch.setattr(instagram, "MAX_REEL_BYTES", 5)
    with pytest.raises(AdsMcpError) as exc:
        instagram.schedule_instagram_reel(_req(_payload(tmp_path)), None)
    assert exc.value.error_code == "REQUEST_INVALID" and "300 MB" in exc.value.message


# ── caption rules (profiled businesses) ──────────────────────────────────────


CAPTION_RULES = {"caption-hook", "caption-https-link", "caption-hashtags", "caption-city"}


def test_schedule_instagram_reel_blocks_caption_without_link(ig_env, social_profile_dir, tmp_path):
    r = instagram.schedule_instagram_reel(_req(_payload(tmp_path)), None)

    assert "approvalId" not in r
    assert r["summary"].startswith("Blocked by business rules")
    assert any(c["rule"] == "caption-https-link" and not c["passed"] for c in r["ruleChecks"])


def test_schedule_instagram_reel_passes_good_caption(ig_env, social_profile_dir, tmp_path):
    r = instagram.schedule_instagram_reel(_req(_payload(tmp_path, caption=GOOD)), None)

    assert r["approvalId"]
    caption_checks = [c for c in r["ruleChecks"] if c["rule"] in CAPTION_RULES]
    assert {c["rule"] for c in caption_checks} == CAPTION_RULES and all(c["passed"] for c in caption_checks)


# ── posting log ──────────────────────────────────────────────────────────────



def _ledger(s3):
    return json.loads(s3.objects["ledger/el-cuis.json"])


def test_instagram_execute_marks_ledger_queued(ig_env, social_profile_dir, tmp_path):
    payload = _payload(tmp_path, caption=GOOD)
    dry = instagram.schedule_instagram_reel(_req(payload), None)
    done = instagram.schedule_instagram_reel(_req(payload, dry["approvalId"]), None)

    p = _ledger(ig_env["s3"])["songs"]["reel"]["platforms"]["instagram"]
    assert p["state"] == "queued" and p["scheduledAt"] == "2026-11-13T02:00:00Z"
    assert p["ref"] == done["data"]["result"]["entryId"]


def test_instagram_execute_failure_marks_ledger_failed(ig_env, social_profile_dir, tmp_path, monkeypatch):
    payload = _payload(tmp_path, caption=GOOD)
    dry = instagram.schedule_instagram_reel(_req(payload), None)

    def boom(**kw):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(instagram.media_queue, "put_video", boom)
    with pytest.raises(RuntimeError):
        instagram.schedule_instagram_reel(_req(payload, dry["approvalId"]), None)

    assert _ledger(ig_env["s3"])["songs"]["reel"]["platforms"]["instagram"]["state"] == "failed"


def test_instagram_execute_without_profile_writes_no_ledger(ig_env, tmp_path):
    payload = _payload(tmp_path)
    dry = instagram.schedule_instagram_reel(_req(payload), None)
    instagram.schedule_instagram_reel(_req(payload, dry["approvalId"]), None)

    assert not any(k.startswith("ledger/") for k in ig_env["s3"].objects)


def test_cancel_marks_ledger_cancelled(ig_env, social_profile_dir):
    social_ledger.mark("el-cuis", song_key="x", sha256="x", platform="instagram", state="queued",
                       scheduled_at="2026-11-13T02:00:00Z", ref="old")
    _, entry_id = _queued()
    dry = instagram.cancel_scheduled_social(_req({"entryId": entry_id}), None)
    instagram.cancel_scheduled_social(_req({"entryId": entry_id}, dry["approvalId"]), None)

    p = _ledger(ig_env["s3"])["songs"]["x"]["platforms"]["instagram"]
    assert p["state"] == "cancelled" and p["ref"] == entry_id


def _break_ledger_writes(s3):
    real = s3.put_object

    def put(**kw):
        if kw["Key"].startswith("ledger/"):
            raise PreconditionFailed("race")
        return real(**kw)

    s3.put_object = put


def test_instagram_execute_ledger_failure_still_returns_result(ig_env, social_profile_dir, tmp_path):
    payload = _payload(tmp_path, caption=GOOD)
    dry = instagram.schedule_instagram_reel(_req(payload), None)
    _break_ledger_writes(ig_env["s3"])
    done = instagram.schedule_instagram_reel(_req(payload, dry["approvalId"]), None)

    result = done["data"]["result"]
    assert result["entryId"] and "ledgerWarning" in result


def test_instagram_cancel_ledger_failure_still_returns_result(ig_env, social_profile_dir):
    social_ledger.mark("el-cuis", song_key="x", sha256="x", platform="instagram", state="queued",
                       scheduled_at="2026-11-13T02:00:00Z", ref="old")
    _, entry_id = _queued()
    dry = instagram.cancel_scheduled_social(_req({"entryId": entry_id}), None)
    _break_ledger_writes(ig_env["s3"])
    done = instagram.cancel_scheduled_social(_req({"entryId": entry_id}, dry["approvalId"]), None)

    assert "ledgerWarning" in done["data"]["result"]
    assert any(k.startswith("queue/cancelled/") for k in ig_env["s3"].objects)
