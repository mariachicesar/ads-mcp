from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from shared import approval_store, media_queue
from shared.models import ToolRequest
from tests.fake_s3 import PreconditionFailed, fake_s3  # noqa: F401  (fixture)
from tests.service_import import load_service_module

NOW = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)  # 08:00 PST


def _load():
    return load_service_module("tiktok", "tools.schedule")


@pytest.fixture
def tt_env(monkeypatch, fake_s3):
    monkeypatch.setattr(approval_store, "_MEMORY_APPROVAL_STORE", approval_store.InMemoryApprovalStore())
    return {"s3": fake_s3, "mod": _load()}


@pytest.fixture
def fixed_now(monkeypatch, tt_env):
    monkeypatch.setattr(tt_env["mod"], "_now", lambda: NOW)
    return NOW


def _req(payload, approval_id=None):
    return ToolRequest(businessKey="el-cuis", approvalId=approval_id, payload=payload)


def _payload(tmp_path, **extra):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"tiktok-bytes")
    return {"filePath": str(f), "scheduledTime": "2026-11-12T18:00:00", "caption": "Serenata sorpresa 🎺 #mariachi", **extra}


def _seed(platform="tiktok", when=NOW + timedelta(days=2), business_key="el-cuis"):
    return media_queue.enqueue({
        "platform": platform, "businessKey": business_key, "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "videoKey": "videos/el-cuis/abc.mp4", "videoSha256": "abc", "caption": "hola", "options": {},
    })


def test_dry_run_no_writes_and_notes_no_caption(tt_env, fixed_now, tmp_path):
    r = tt_env["mod"].schedule_inbox_video(_req(_payload(tmp_path)), None)

    assert r["approvalId"]
    assert r["service"] == "tiktok"
    assert "inbox" in r["summary"].lower()
    assert "caption" in r["summary"].lower()
    assert tt_env["s3"].objects == {}


def test_execute_enqueues_tiktok_entry(tt_env, fixed_now, tmp_path):
    mod = tt_env["mod"]
    payload = _payload(tmp_path)
    dry = mod.schedule_inbox_video(_req(payload), None)
    done = mod.schedule_inbox_video(_req(payload, dry["approvalId"]), None)

    keys = sorted(tt_env["s3"].objects)
    assert len(keys) == 2 and keys[0].startswith("queue/pending/20261113T0200Z-") and keys[1].startswith("videos/el-cuis/")
    entry = json.loads(tt_env["s3"].objects[keys[0]])
    assert entry["platform"] == "tiktok" and entry["scheduledAt"] == "2026-11-13T02:00:00Z"
    assert entry["caption"] == "Serenata sorpresa 🎺 #mariachi" and entry["options"] == {}
    assert entry["videoKey"] == keys[1]
    assert done["data"]["result"]["entryId"] == entry["id"]


def test_daily_limit_blocks_sixth(tt_env, fixed_now, tmp_path):
    for hours in range(5):
        _seed(when=datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc) + timedelta(hours=hours - 2))
    _seed(platform="instagram", when=datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc))

    r = tt_env["mod"].schedule_inbox_video(_req(_payload(tmp_path)), None)

    assert "approvalId" not in r
    assert any(c["rule"] == "tiktok-daily-limit" and not c["passed"] for c in r["ruleChecks"])


def test_daily_limit_allows_fifth(tt_env, fixed_now, tmp_path):
    for hours in range(4):
        _seed(when=datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc) + timedelta(hours=hours))
    _seed(when=datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc))  # 25 h later: outside the window

    r = tt_env["mod"].schedule_inbox_video(_req(_payload(tmp_path)), None)

    assert r["approvalId"]


def test_cancel_only_pending(tt_env):
    mod = tt_env["mod"]
    pending_key = _seed()
    done_key = media_queue.move(_seed(), "done")
    pending_id = json.loads(tt_env["s3"].objects[pending_key])["id"]
    done_id = json.loads(tt_env["s3"].objects[done_key])["id"]

    blocked = mod.cancel_scheduled(_req({"entryId": done_id}), None)
    assert "approvalId" not in blocked

    dry = mod.cancel_scheduled(_req({"entryId": pending_id}), None)
    mod.cancel_scheduled(_req({"entryId": pending_id}, dry["approvalId"]), None)
    assert not any(k.startswith("queue/pending/") for k in tt_env["s3"].objects)
    assert any(k.startswith("queue/cancelled/") for k in tt_env["s3"].objects)


def test_cancel_refuses_other_platform(tt_env):
    ig_key = _seed(platform="instagram")
    ig_id = json.loads(tt_env["s3"].objects[ig_key])["id"]

    with pytest.raises(Exception, match="No scheduled TikTok"):
        tt_env["mod"].cancel_scheduled(_req({"entryId": ig_id}), None)


def test_list_shows_caption_and_pacific_time(tt_env):
    _seed(when=datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc))
    _seed(platform="instagram")

    r = tt_env["mod"].list_scheduled(_req({}), None)

    entries = r["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["caption"] == "hola"
    assert entries[0]["scheduledPacific"].startswith("2026-11-12T18:00:00-08:00")
    assert entries[0]["state"] == "pending"


def test_meta_publish_helpers_still_importable():
    publish = load_service_module("meta-ads", "tools.publish")
    from shared import scheduling

    assert callable(publish._video_source)
    assert publish._parse_schedule_time is scheduling.parse_schedule_time
    assert publish._sha256 is scheduling.sha256_file
    assert callable(publish._schedule_window_check)


# ── caption rules (profiled businesses) ──────────────────────────────────────

from tests.social_fixtures import GOOD  # noqa: E402

CAPTION_RULES = {"caption-hook", "caption-https-link", "caption-hashtags", "caption-city"}


def test_inbox_video_blocks_caption_without_link(tt_env, fixed_now, social_profile_dir, tmp_path):
    r = tt_env["mod"].schedule_inbox_video(_req(_payload(tmp_path)), None)

    assert "approvalId" not in r
    assert r["summary"].startswith("Blocked by business rules")
    assert any(c["rule"] == "caption-https-link" and not c["passed"] for c in r["ruleChecks"])


def test_inbox_video_missing_caption_blocked_with_profile(tt_env, fixed_now, social_profile_dir, tmp_path):
    payload = _payload(tmp_path)
    del payload["caption"]
    r = tt_env["mod"].schedule_inbox_video(_req(payload), None)

    assert "approvalId" not in r
    assert any(c["rule"] == "caption-hook" and not c["passed"] for c in r["ruleChecks"])


def test_inbox_video_passes_good_caption(tt_env, fixed_now, social_profile_dir, tmp_path):
    r = tt_env["mod"].schedule_inbox_video(_req(_payload(tmp_path, caption=GOOD)), None)

    assert r["approvalId"]
    caption_checks = [c for c in r["ruleChecks"] if c["rule"] in CAPTION_RULES]
    assert {c["rule"] for c in caption_checks} == CAPTION_RULES and all(c["passed"] for c in caption_checks)


# ── posting log ──────────────────────────────────────────────────────────────

from shared import social_ledger  # noqa: E402


def _ledger(s3):
    return json.loads(s3.objects["ledger/el-cuis.json"])


def test_tiktok_execute_marks_ledger_queued(tt_env, fixed_now, social_profile_dir, tmp_path):
    mod = tt_env["mod"]
    payload = _payload(tmp_path, caption=GOOD)
    dry = mod.schedule_inbox_video(_req(payload), None)
    done = mod.schedule_inbox_video(_req(payload, dry["approvalId"]), None)

    p = _ledger(tt_env["s3"])["songs"]["clip"]["platforms"]["tiktok"]
    assert p["state"] == "queued" and p["scheduledAt"] == "2026-11-13T02:00:00Z"
    assert p["ref"] == done["data"]["result"]["entryId"]


def test_tiktok_execute_without_profile_writes_no_ledger(tt_env, fixed_now, tmp_path):
    mod = tt_env["mod"]
    payload = _payload(tmp_path)
    dry = mod.schedule_inbox_video(_req(payload), None)
    mod.schedule_inbox_video(_req(payload, dry["approvalId"]), None)

    assert not any(k.startswith("ledger/") for k in tt_env["s3"].objects)


def test_cancel_marks_ledger_cancelled(tt_env, social_profile_dir):
    social_ledger.mark("el-cuis", song_key="x", sha256="abc", platform="tiktok", state="queued",
                       scheduled_at="2026-11-13T02:00:00Z", ref="old")
    entry_id = json.loads(tt_env["s3"].objects[_seed()])["id"]
    mod = tt_env["mod"]
    dry = mod.cancel_scheduled(_req({"entryId": entry_id}), None)
    mod.cancel_scheduled(_req({"entryId": entry_id}, dry["approvalId"]), None)

    p = _ledger(tt_env["s3"])["songs"]["x"]["platforms"]["tiktok"]
    assert p["state"] == "cancelled" and p["ref"] == entry_id


def test_tiktok_execute_ledger_failure_still_returns_result(tt_env, fixed_now, social_profile_dir, tmp_path):
    mod = tt_env["mod"]
    payload = _payload(tmp_path, caption=GOOD)
    dry = mod.schedule_inbox_video(_req(payload), None)
    real = tt_env["s3"].put_object

    def put(**kw):
        if kw["Key"].startswith("ledger/"):
            raise PreconditionFailed("race")
        return real(**kw)

    tt_env["s3"].put_object = put
    done = mod.schedule_inbox_video(_req(payload, dry["approvalId"]), None)

    assert done["data"]["result"]["entryId"] and "ledgerWarning" in done["data"]["result"]
