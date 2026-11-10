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


# ── scheduling helpers + schedule_page_video ─────────────────────────────────

from datetime import datetime, timezone  # noqa: E402

NOW = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)  # 08:00 PST
NOON_NOV12_PT = 1794513600  # 2026-11-12T12:00:00-08:00


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(publish, "_now", lambda: NOW)


def _sched(tmp_path, **extra):
    return {"filePath": str(_video(tmp_path)), "scheduledTime": "2026-11-12T12:00:00",
            "description": "Mariachi El Cuis 🎺", **extra}


def test_parse_schedule_time_naive_is_pacific_after_dst():
    assert publish._parse_schedule_time("2026-11-01T09:00:00", "t") == datetime(2026, 11, 1, 17, 0, tzinfo=timezone.utc)


def test_parse_schedule_time_keeps_explicit_offset():
    assert publish._parse_schedule_time("2026-11-12T12:00:00-08:00", "t").hour == 20


def test_parse_schedule_time_rejects_garbage():
    with pytest.raises(AdsMcpError):
        publish._parse_schedule_time("next tuesday", "t")


def test_schedule_page_video_dry_run_fields(publish_env, fixed_now, tmp_path):
    r = publish.schedule_page_video(_req(_sched(tmp_path, title="Serenata")), None)
    fields = r["data"]["graphRequest"]["fields"]

    assert r["approvalId"]
    assert r["data"]["graphRequest"]["path"] == "999/videos"
    assert fields["scheduled_publish_time"] == NOON_NOV12_PT
    assert fields["published"] is False
    assert fields["title"] == "Serenata" and fields["description"] == "Mariachi El Cuis 🎺"
    assert publish_env["calls"] == [] and publish_env["posts"] == []


@pytest.mark.parametrize("when", ["2026-11-10T08:05:00", "2026-11-09T12:00:00", "2027-02-01T12:00:00"])
def test_schedule_page_video_blocks_out_of_window_times(publish_env, fixed_now, tmp_path, when):
    r = publish.schedule_page_video(_req(_sched(tmp_path, scheduledTime=when)), None)

    assert "approvalId" not in r
    assert any(c["rule"] == "schedule-window" and not c["passed"] for c in r["ruleChecks"])


def test_schedule_page_video_blocks_other_page(publish_env, fixed_now, tmp_path):
    r = publish.schedule_page_video(_req(_sched(tmp_path, pageId="555")), None)

    assert "approvalId" not in r
    assert any(c["rule"] == "page-is-configured" and not c["passed"] for c in r["ruleChecks"])


def test_schedule_page_video_execute_uses_page_token(publish_env, fixed_now, tmp_path):
    payload = _sched(tmp_path)
    dry = publish.schedule_page_video(_req(payload), None)
    done = publish.schedule_page_video(_req(payload, dry["approvalId"]), None)

    assert done["executed"] is True
    assert publish_env["calls"] == ["token:999"]
    assert publish_env["tokens"] == ["page-tok"]
    assert publish_env["posts"][0][0] == "999/videos"
    assert done["data"]["result"] == {"videoId": "new-video"}


# ── schedule_page_reel ───────────────────────────────────────────────────────


@pytest.fixture
def reel_env(publish_env, monkeypatch):
    publish_env["finish_error"] = None

    def graph_request(config, method, path, *, params=None, data=None, access_token=None, tool=None, **kw):
        assert path == "999/video_reels" and access_token == "page-tok"
        if data["upload_phase"] == "start":
            publish_env["calls"].append("start")
            return {"video_id": "vid1", "upload_url": "https://rupload.facebook.com/video-upload/v23.0/vid1"}
        publish_env["calls"].append(f"finish:{data['video_id']}:{data['video_state']}:{data['scheduled_publish_time']}")
        if publish_env["finish_error"]:
            raise publish_env["finish_error"]
        return {"success": True}

    def upload_reel_bytes(config, *, video_id, access_token, file_path, file_url, tool=None):
        publish_env["calls"].append(f"upload:{video_id}")
        return {"success": True}

    monkeypatch.setattr(publish, "_graph_request", graph_request)
    monkeypatch.setattr(publish, "upload_reel_bytes", upload_reel_bytes)
    return publish_env


def test_schedule_page_reel_calls_start_upload_finish_in_order(reel_env, fixed_now, tmp_path):
    payload = _sched(tmp_path)
    dry = publish.schedule_page_reel(_req(payload), None)
    done = publish.schedule_page_reel(_req(payload, dry["approvalId"]), None)

    assert reel_env["calls"] == ["token:999", "start", "upload:vid1", f"finish:vid1:SCHEDULED:{NOON_NOV12_PT}"]
    assert done["data"]["result"]["videoId"] == "vid1"


def test_schedule_page_reel_finish_failure_reports_video_id(reel_env, fixed_now, tmp_path):
    reel_env["finish_error"] = AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message="boom", tool="t")
    payload = _sched(tmp_path)
    dry = publish.schedule_page_reel(_req(payload), None)

    with pytest.raises(AdsMcpError) as exc:
        publish.schedule_page_reel(_req(payload, dry["approvalId"]), None)

    assert exc.value.details["videoId"] == "vid1"
    assert exc.value.details["note"] == "Reel bytes were uploaded but the reel was not scheduled."


def test_schedule_page_reel_dry_run_makes_no_calls(reel_env, fixed_now, tmp_path):
    dry = publish.schedule_page_reel(_req(_sched(tmp_path)), None)

    assert dry["approvalId"]
    assert [s["step"] for s in dry["data"]["graphRequests"]] == [1, 2, 3]
    assert reel_env["calls"] == []


# ── list_scheduled_page_videos + registration ────────────────────────────────


def test_list_scheduled_page_videos_filters_and_converts(publish_env, monkeypatch):
    rows = [
        {"id": "v1", "title": "keep", "published": False, "scheduled_publish_time": NOON_NOV12_PT},
        {"id": "v2", "title": "live", "published": True},
        {"id": "v3", "title": "draft", "published": False},
    ]

    def graph_request(config, method, path, *, params=None, access_token=None, tool=None, **kw):
        assert (method, path, access_token) == ("GET", "999/videos", "page-tok")
        return {"data": rows}

    monkeypatch.setattr(publish, "_graph_request", graph_request)
    r = publish.list_scheduled_page_videos(_req({}), None)

    assert r["mode"] == "read"
    assert [v["id"] for v in r["data"]["videos"]] == ["v1"]
    assert r["data"]["videos"][0]["scheduledPacific"] == "2026-11-12T12:00:00-08:00"


def test_list_scheduled_page_videos_blocks_other_page(publish_env):
    with pytest.raises(AdsMcpError):
        publish.list_scheduled_page_videos(_req({"pageId": "555"}), None)


def test_http_routes_registered():
    main = load_service_module("meta-ads", "main")
    paths = {r.path for r in main.app.routes}
    assert {"/tools/upload_ad_video", "/tools/schedule_page_video",
            "/tools/schedule_page_reel", "/tools/list_scheduled_page_videos"} <= paths


def test_mcp_tools_registered():
    server = load_service_module("meta-ads", "mcp_server")
    for name in ("meta_ads_upload_ad_video", "meta_ads_schedule_page_video",
                 "meta_ads_schedule_page_reel", "meta_ads_list_scheduled_page_videos"):
        assert hasattr(server, name)


def test_list_scheduled_page_videos_accepts_graph_datetime_strings(publish_env, monkeypatch):
    rows = [
        {"id": "late", "published": False, "scheduled_publish_time": "2026-11-13T20:00:00+0000"},
        {"id": "early", "published": False, "scheduled_publish_time": NOON_NOV12_PT},
    ]
    monkeypatch.setattr(publish, "_graph_request", lambda *a, **k: {"data": rows})

    videos = publish.list_scheduled_page_videos(_req({}), None)["data"]["videos"]

    assert [v["id"] for v in videos] == ["early", "late"]
    assert videos[1]["scheduledPacific"] == "2026-11-13T12:00:00-08:00"
