from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared import approval_store, media_queue, social_ledger, video_probe
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.fake_s3 import fake_s3  # noqa: F401  (fixture)
from tests.service_import import load_service_module

NOW = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)  # 08:00 PST
SLOT = "2026-11-19T19:00:00"  # Pacific -> 2026-11-20T03:00:00Z
SLOT_Z = "2026-11-20T03:00:00Z"
SUMMARY = ("¿Quinceañera en Downey? Así suena 'Hermoso Cariño' en vivo 🎺\n"
           "Hagamos tu celebración inolvidable con Mariachi El Cuis.")


def _load():
    return load_service_module("gbp", "tools.social_post")


@pytest.fixture
def gbp_env(monkeypatch, fake_s3, tmp_path):
    monkeypatch.setattr(approval_store, "_MEMORY_APPROVAL_STORE", approval_store.InMemoryApprovalStore())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    frames: list[tuple[str, str, float]] = []

    def fake_extract(path, out_path, *, at_seconds):
        frames.append((path, out_path, at_seconds))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"jpeg-" + str(at_seconds).encode())
        return out_path

    monkeypatch.setattr(video_probe, "probe", lambda p: {"width": 1080, "height": 1920, "seconds": 40.0})
    monkeypatch.setattr(video_probe, "extract_frame", fake_extract)
    mod = _load()
    monkeypatch.setattr(mod, "_now", lambda: NOW)
    return {"s3": fake_s3, "mod": mod, "frames": frames, "tmp": tmp_path}


def _req(payload, approval_id=None):
    return ToolRequest(businessKey="el-cuis", approvalId=approval_id, dryRun=approval_id is None, payload=payload)


def _payload(tmp_path, **extra):
    f = tmp_path / "Hermoso Cariño Reel.mp4"
    if not f.exists():
        f.write_bytes(b"video-bytes")
    return {"filePath": str(f), "scheduledTime": SLOT, "summary": SUMMARY, **extra}


def _failed(r):
    return {c["rule"] for c in r["ruleChecks"] if not c["passed"]}


def test_dry_run_returns_frame_path_and_approval(gbp_env, social_profile_dir):
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"])), None)

    assert r["approvalId"] and r["mode"] == "dry-run" and r["service"] == "gbp"
    frame = r["data"]["framePath"]
    expected_dir = gbp_env["tmp"] / "localappdata" / "ads-mcp" / "frames"
    assert Path(frame).parent == expected_dir and Path(frame).is_file()
    assert gbp_env["frames"][0][2] == 10.0  # 25% of 40 s
    sha = r["data"]["file"]["sha256"]
    assert Path(frame).name == f"{sha}-10.jpg"
    entry = r["data"]["entry"]
    assert entry["platform"] == "gbp" and entry["scheduledAt"] == SLOT_Z and entry["songKey"] == "hermosocarino"
    assert not any(k.startswith(("queue/", "images/")) for k in gbp_env["s3"].objects)


def test_frames_dir_falls_back_to_temp(gbp_env, monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "tmpdir"))
    assert Path(gbp_env["mod"]._frames_dir()) == tmp_path / "tmpdir" / "ads-mcp" / "frames"


def test_frame_seconds_overrides_default(gbp_env):
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"], frameSeconds=3.5)), None)

    assert gbp_env["frames"][0][2] == 3.5
    assert r["data"]["framePath"].endswith("-3.5.jpg")


@pytest.mark.parametrize("phone", ["626-922-0091", "(626) 922-0091", "626/922/0091", "626–922–0091",
                                   "+1 626 922 0091", "6269220091", "llama al 818-555-1234"])
def test_phone_in_summary_blocks(gbp_env, social_profile_dir, phone):
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"], summary=SUMMARY + "\n" + phone)), None)

    assert "approvalId" not in r
    assert "gbp-no-phone" in _failed(r)


def test_summary_without_phone_passes_all_rules(gbp_env, social_profile_dir):
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"])), None)

    assert _failed(r) == set()
    assert {"caption-hook", "gbp-no-phone", "gbp-length", "gbp-frame", "schedule-window"} <= {
        c["rule"] for c in r["ruleChecks"]}


def test_long_summary_blocks_on_length(gbp_env, social_profile_dir):
    long = "Serenata en Downey 🎺\n" + "a" * 1400
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"], summary=long)), None)

    assert "approvalId" not in r
    assert "gbp-length" in _failed(r)


def test_length_budget_leaves_room_for_link_line(gbp_env):
    mod = gbp_env["mod"]
    ok = "Hook\n" + "a" * (1500 - mod.LINK_LINE_BUDGET - 5)
    r = mod.schedule_social_post(_req(_payload(gbp_env["tmp"], summary=ok)), None)
    assert "gbp-length" not in _failed(r)
    r = mod.schedule_social_post(_req(_payload(gbp_env["tmp"], summary=ok + "a")), None)
    assert "gbp-length" in _failed(r)


def test_hook_rule_applies(gbp_env, social_profile_dir):
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"], summary="#mariachi\nhola")), None)

    assert "caption-hook" in _failed(r)


def test_ffmpeg_missing_blocks_on_frame(gbp_env, monkeypatch):
    def boom(path, out_path, *, at_seconds):
        raise RuntimeError("ffmpeg is not installed or not on PATH.")

    monkeypatch.setattr(video_probe, "extract_frame", boom)
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"])), None)

    assert "approvalId" not in r
    check = next(c for c in r["ruleChecks"] if c["rule"] == "gbp-frame")
    assert not check["passed"] and check["severity"] == "blocking"
    assert "ffmpeg is needed to make the GBP photo" in check["message"]
    assert r["data"]["framePath"] is None


def test_ffprobe_missing_without_frame_seconds_blocks(gbp_env, monkeypatch):
    monkeypatch.setattr(video_probe, "probe", lambda p: None)
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"])), None)

    check = next(c for c in r["ruleChecks"] if c["rule"] == "gbp-frame")
    assert not check["passed"] and "ffmpeg is needed to make the GBP photo" in check["message"]
    assert gbp_env["frames"] == []


def test_frame_seconds_past_end_blocks(gbp_env):
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"], frameSeconds=41)), None)

    assert "gbp-frame" in _failed(r)


def test_negative_frame_seconds_rejected(gbp_env):
    with pytest.raises(AdsMcpError, match="frameSeconds"):
        gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"], frameSeconds=-1)), None)


def test_summary_required(gbp_env):
    with pytest.raises(AdsMcpError, match="summary"):
        gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"], summary="  ")), None)


def test_execute_puts_image_enqueues_and_marks_ledger(gbp_env, social_profile_dir):
    mod, s3 = gbp_env["mod"], gbp_env["s3"]
    payload = _payload(gbp_env["tmp"])
    dry = mod.schedule_social_post(_req(payload), None)
    done = mod.schedule_social_post(_req(payload, dry["approvalId"]), None)

    sha = dry["data"]["file"]["sha256"]
    frame_sha = hashlib.sha256(Path(dry["data"]["framePath"]).read_bytes()).hexdigest()
    assert done["executed"] is True
    images = [k for k in s3.objects if k.startswith("images/")]
    assert images == [f"images/el-cuis/{frame_sha}.jpg"]  # keyed by the photo, so a new frame is a new image
    assert s3.objects[images[0]] == b"jpeg-10.0"
    queued = [k for k in s3.objects if k.startswith("queue/pending/")]
    assert len(queued) == 1 and queued[0].startswith("queue/pending/20261120T0300Z-")
    entry = json.loads(s3.objects[queued[0]])
    expected = {"platform": "gbp", "businessKey": "el-cuis", "scheduledAt": SLOT_Z, "videoSha256": sha,
                "imageKey": images[0], "summary": SUMMARY, "songKey": "hermosocarino"}
    assert {k: entry[k] for k in expected} == expected
    assert not any(k.startswith("videos/") for k in s3.objects)
    assert done["data"]["result"]["entryId"] == entry["id"]
    ledger = json.loads(s3.objects["ledger/el-cuis.json"])
    p = ledger["songs"]["hermosocarino"]["platforms"]["gbp"]
    assert p["state"] == "queued" and p["ref"] == entry["id"] and p["scheduledAt"] == SLOT_Z


def test_execute_without_profile_writes_no_ledger(gbp_env):
    mod, s3 = gbp_env["mod"], gbp_env["s3"]
    payload = _payload(gbp_env["tmp"])
    dry = mod.schedule_social_post(_req(payload), None)
    mod.schedule_social_post(_req(payload, dry["approvalId"]), None)

    assert any(k.startswith("queue/pending/") for k in s3.objects)
    assert not any(k.startswith("ledger/") for k in s3.objects)


def test_execute_with_different_frame_seconds_rejected(gbp_env):
    mod = gbp_env["mod"]
    dry = mod.schedule_social_post(_req(_payload(gbp_env["tmp"])), None)

    with pytest.raises(AdsMcpError):
        mod.schedule_social_post(_req(_payload(gbp_env["tmp"], frameSeconds=2), dry["approvalId"]), None)


def test_execute_rejected_after_video_edit(gbp_env):
    mod = gbp_env["mod"]
    payload = _payload(gbp_env["tmp"])
    dry = mod.schedule_social_post(_req(payload), None)
    Path(payload["filePath"]).write_bytes(b"edited")

    with pytest.raises(AdsMcpError):
        mod.schedule_social_post(_req(payload, dry["approvalId"]), None)


def test_window_check_blocks_past_slot(gbp_env):
    r = gbp_env["mod"].schedule_social_post(_req(_payload(gbp_env["tmp"], scheduledTime="2026-11-12T08:05:00")), None)

    assert "schedule-window" in _failed(r)


# ── list / cancel ────────────────────────────────────────────────────────────

def _seed(platform="gbp", when=NOW + timedelta(days=2)):
    return media_queue.enqueue({
        "platform": platform, "businessKey": "el-cuis", "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "videoSha256": "abc", "imageKey": "images/el-cuis/abc.jpg", "summary": "hola", "songKey": "x",
    })


def test_list_shows_gbp_entries_only(gbp_env):
    _seed(when=datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc))
    _seed(platform="tiktok")

    r = gbp_env["mod"].list_social_posts(_req({}), None)

    entries = r["data"]["entries"]
    assert len(entries) == 1 and entries[0]["summary"] == "hola" and entries[0]["state"] == "pending"
    assert entries[0]["scheduledPacific"].startswith("2026-11-12T18:00:00-08:00")


def test_cancel_moves_pending_and_marks_ledger(gbp_env, social_profile_dir):
    mod, s3 = gbp_env["mod"], gbp_env["s3"]
    entry_id = json.loads(s3.objects[_seed()])["id"]
    social_ledger.mark("el-cuis", song_key="x", sha256="abc", platform="gbp", state="queued",
                       scheduled_at="2026-11-14T16:00:00Z", ref=entry_id)

    dry = mod.cancel_social_post(_req({"entryId": entry_id}), None)
    mod.cancel_social_post(_req({"entryId": entry_id}, dry["approvalId"]), None)

    assert not any(k.startswith("queue/pending/") for k in s3.objects)
    assert any(k.startswith("queue/cancelled/") for k in s3.objects)
    p = json.loads(s3.objects["ledger/el-cuis.json"])["songs"]["x"]["platforms"]["gbp"]
    assert p["state"] == "cancelled"


def test_cancel_only_pending_and_only_gbp(gbp_env):
    mod, s3 = gbp_env["mod"], gbp_env["s3"]
    done_id = json.loads(s3.objects[media_queue.move(_seed(), "done")])["id"]
    assert "approvalId" not in mod.cancel_social_post(_req({"entryId": done_id}), None)

    tt_id = json.loads(s3.objects[_seed(platform="tiktok")])["id"]
    with pytest.raises(AdsMcpError, match="No scheduled GBP"):
        mod.cancel_social_post(_req({"entryId": tt_id}), None)


def test_mcp_server_registers_tools_and_keeps_gq_tools():
    server = load_service_module("gbp", "mcp_server")
    for name in ("gbp_schedule_social_post", "gbp_list_social_posts", "gbp_cancel_social_post",
                 "gbp_create_post", "gbp_list_scheduled_posts", "gbp_cancel_scheduled_post"):
        assert getattr(server, name) is not None
