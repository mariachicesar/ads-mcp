from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from shared import approval_store, media_queue, social_ledger, video_probe
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.fake_s3 import fake_s3  # noqa: F401  (fixture)
from tests.service_import import load_service_module
from tests.social_fixtures import GOOD

NOW = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)  # 08:00 PST
SLOT = "2026-11-19T19:00:00"  # Pacific -> 2026-11-20T03:00:00Z
SLOT_Z = "2026-11-20T03:00:00Z"
TITLE = "Reloj | Mariachi en vivo para tu fiesta en Los Ángeles"
CAPTION_WITH_PHONE = GOOD


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Insert:
    def __init__(self, yt, video_id, chunks=2):
        self.yt, self.video_id, self.chunks = yt, video_id, chunks

    def next_chunk(self, **kwargs):
        self.yt.chunk_calls.append(kwargs)
        self.chunks -= 1
        if self.chunks <= 0 and self.yt.chunk_error:
            raise self.yt.chunk_error  # e.g. a timeout on the final chunk
        return (None, {"id": self.video_id}) if self.chunks <= 0 else ("progress", None)


class FakeYouTube:
    def __init__(self):
        self.inserted, self.listed, self.deleted = [], [], []
        self.videos_by_id: dict[str, dict] = {}
        self.upload_status = "uploaded"
        self.next_id = "vid123"
        self.list_error: Exception | None = None
        self.chunk_error: Exception | None = None
        self.chunk_calls: list[dict] = []

    def videos(self):
        return self

    def insert(self, **kwargs):
        self.inserted.append(kwargs)
        body = kwargs["body"]
        self.videos_by_id[self.next_id] = {
            "id": self.next_id, "snippet": dict(body["snippet"]),
            "status": {**body["status"], "uploadStatus": self.upload_status},
        }
        return _Insert(self, self.next_id)

    def list(self, **kwargs):
        self.listed.append(kwargs)

        def run():
            if self.list_error:
                raise self.list_error
            ids = kwargs["id"].split(",")
            return {"items": [self.videos_by_id[i] for i in ids if i in self.videos_by_id]}
        return _Exec(run)

    def delete(self, **kwargs):
        self.deleted.append(kwargs)
        return _Exec(lambda: self.videos_by_id.pop(kwargs["id"], None) and "")


@pytest.fixture
def mod(monkeypatch, fake_s3):
    monkeypatch.setattr(approval_store, "_MEMORY_APPROVAL_STORE", approval_store.InMemoryApprovalStore())
    m = load_service_module("youtube", "tools.schedule")
    monkeypatch.setattr(m, "_now", lambda: NOW)
    monkeypatch.setattr(video_probe, "probe", lambda p: {"width": 1080, "height": 1920, "seconds": 45.0})
    return m


@pytest.fixture
def fake_yt(mod, monkeypatch):
    yt = FakeYouTube()
    monkeypatch.setattr(mod, "_youtube", lambda business_key, tool: yt)
    uploads = []
    monkeypatch.setattr(mod, "MediaFileUpload", lambda path, **kw: uploads.append((path, kw)) or ("media", path))
    yt.uploads = uploads
    return yt


@pytest.fixture
def video(tmp_path):
    f = tmp_path / "Reloj Reel.mp4"
    f.write_bytes(b"youtube-bytes")
    return f


@pytest.fixture
def ctx(mod, fake_yt, video, fake_s3):
    return {"mod": mod, "yt": fake_yt, "video": video, "s3": fake_s3}


def _payload(ctx, **extra):
    return {"filePath": str(ctx["video"]), "scheduledTime": SLOT, "title": TITLE,
            "description": CAPTION_WITH_PHONE, **extra}


def _req(payload, approval_id=None):
    return ToolRequest(businessKey="el-cuis", approvalId=approval_id, dryRun=approval_id is None, payload=payload)


@pytest.fixture
def dry(ctx):
    def run(**extra):
        return ctx["mod"].schedule_video(_req(_payload(ctx, **extra)), None)
    return run


@pytest.fixture
def execute(ctx):
    def run(**extra):
        payload = _payload(ctx, **extra)
        d = ctx["mod"].schedule_video(_req(payload), None)
        assert d["approvalId"], d["ruleChecks"]
        return ctx["mod"].schedule_video(_req(payload, d["approvalId"]), None)
    return run


def _ledger(s3):
    return json.loads(s3.objects["ledger/el-cuis.json"])


def _logged(s3, platform="youtube"):
    return _ledger(s3)["songs"]["reloj"]["platforms"][platform]


def _queue(state):
    return media_queue.list_entries(states=(state,), business_key="el-cuis", platform="youtube")


# --- schedule: dry run -------------------------------------------------------------------------

def test_dry_run_checks_title_phone_and_window(ctx, dry, social_profile_dir):
    r = dry()

    assert r["mode"] == "dry-run" and r["approvalId"]
    assert {c["rule"] for c in r["ruleChecks"]} >= {"youtube-title", "phone", "caption-hook", "schedule-window"}
    assert all(c["passed"] for c in r["ruleChecks"])
    assert ctx["yt"].inserted == [] and ctx["s3"].objects == {}
    assert r["data"]["short"] is True
    assert r["data"]["upload"]["status"]["publishAt"] == SLOT_Z


@pytest.mark.parametrize("title", ["", "x" * 101, "Mariachi | Reloj", "Reloj <live>"])
def test_bad_title_blocks(title, dry, social_profile_dir):
    r = dry(title=title)

    assert r.get("approvalId") is None
    assert not next(c for c in r["ruleChecks"] if c["rule"] == "youtube-title")["passed"]


def test_title_matches_song_case_and_accent_insensitively(ctx, tmp_path, social_profile_dir):
    f = tmp_path / "Canción Mixteca Post (2).mp4"
    f.write_bytes(b"x")
    r = ctx["mod"].schedule_video(_req(_payload(ctx, filePath=str(f), title="CANCION mixteca | En vivo")), None)

    assert next(c for c in r["ruleChecks"] if c["rule"] == "youtube-title")["passed"]


def test_description_without_phone_blocks(dry, social_profile_dir):
    r = dry(description=GOOD.replace("📞 626-922-0091\n", ""))

    assert r.get("approvalId") is None
    assert not next(c for c in r["ruleChecks"] if c["rule"] == "phone")["passed"]


@pytest.mark.parametrize("description", ["x" * 5001, GOOD + " <b>"])
def test_bad_description_is_invalid(description, dry):
    with pytest.raises(AdsMcpError) as err:
        dry(description=description)
    assert err.value.error_code == "REQUEST_INVALID"


def test_file_url_rejected(ctx):
    with pytest.raises(AdsMcpError):
        ctx["mod"].schedule_video(_req({"fileUrl": "https://x/y.mp4", "scheduledTime": SLOT, "title": TITLE}), None)


def test_slot_outside_window_blocks(dry):
    assert dry(scheduledTime="2026-11-12T08:05:00").get("approvalId") is None


def test_shorts_flag(dry, monkeypatch):
    monkeypatch.setattr(video_probe, "probe", lambda p: {"width": 1080, "height": 1920, "seconds": 204.6})
    assert dry()["data"]["short"] is False   # >180 s posts as regular video
    monkeypatch.setattr(video_probe, "probe", lambda p: {"width": 1920, "height": 1080, "seconds": 30.0})
    assert dry()["data"]["short"] is False   # horizontal
    monkeypatch.setattr(video_probe, "probe", lambda p: None)
    r = dry()
    assert r["data"]["short"] is None and r["approvalId"]   # ffprobe missing: still allowed


# --- schedule: execute -------------------------------------------------------------------------

def test_execute_uploads_private_with_publish_at_and_logs(ctx, execute, social_profile_dir):
    r = execute()

    body = ctx["yt"].inserted[0]["body"]
    assert body["status"] == {"privacyStatus": "private", "publishAt": SLOT_Z, "selfDeclaredMadeForKids": False}
    assert body["snippet"] == {"title": TITLE, "description": CAPTION_WITH_PHONE, "categoryId": "10"}
    assert ctx["yt"].uploads[0] == (str(ctx["video"]), {"chunksize": -1, "resumable": True})
    result = r["data"]["result"]
    assert result["videoId"] == "vid123" and result["url"] == "https://youtu.be/vid123" and result["short"] is True
    assert _logged(ctx["s3"])["ref"] == "vid123" and _logged(ctx["s3"])["state"] == "queued"
    assert _logged(ctx["s3"])["scheduledAt"] == SLOT_Z
    [entry] = _queue("pending")
    assert result["verifyQueueKey"] == entry["_key"]
    assert {k: entry[k] for k in ("platform", "businessKey", "scheduledAt", "videoId", "publishAt")} == {
        "platform": "youtube", "businessKey": "el-cuis", "scheduledAt": "2026-11-20T04:00:00Z",
        "videoId": "vid123", "publishAt": SLOT_Z}
    assert entry["videoSha256"] == _logged(ctx["s3"])["sha256"]


def test_execute_ledger_failure_returns_warning_and_keeps_verify_entry(ctx, execute, social_profile_dir, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("s3 down")
    monkeypatch.setattr(social_ledger, "mark", boom)

    r = execute()

    assert "ledgerWarning" in r["data"]["result"] and len(_queue("pending")) == 1


@pytest.mark.parametrize("status", ["rejected", "failed"])
def test_rejected_upload_marks_failed(ctx, execute, social_profile_dir, status):
    ctx["yt"].upload_status = status

    with pytest.raises(AdsMcpError) as err:
        execute()

    assert err.value.retryable is False
    assert _logged(ctx["s3"])["state"] == "failed"
    assert ctx["yt"].deleted == [{"id": "vid123"}]
    assert _queue("pending") == []


def test_status_check_error_after_upload_still_queues(ctx, execute, social_profile_dir):
    ctx["yt"].list_error = RuntimeError("timeout")

    r = execute()

    assert r["data"]["result"]["videoId"] == "vid123" and _logged(ctx["s3"])["state"] == "queued"


def test_insert_error_is_upstream_and_marks_failed(ctx, execute, social_profile_dir, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("quotaExceeded")
    monkeypatch.setattr(ctx["yt"], "insert", broken)

    with pytest.raises(AdsMcpError) as err:
        execute()

    assert err.value.error_code == "UPSTREAM_ERROR" and _logged(ctx["s3"])["state"] == "failed"


def test_verify_enqueue_failure_after_upload_keeps_ledger_queued(ctx, execute, social_profile_dir, monkeypatch):
    def boom(entry):
        raise RuntimeError("s3 down")
    monkeypatch.setattr(media_queue, "enqueue", boom)

    r = execute()

    result = r["data"]["result"]
    assert result["videoId"] == "vid123" and result["verifyQueueKey"] is None and "verifyWarning" in result
    assert _logged(ctx["s3"])["state"] == "queued"


# --- list ----------------------------------------------------------------------------------------

def test_list_joins_ledger_with_youtube_status(ctx, execute, social_profile_dir):
    execute()

    r = ctx["mod"].list_scheduled(ToolRequest(businessKey="el-cuis", payload={}), None)

    [row] = r["data"]["videos"]
    assert row["videoId"] == "vid123" and row["songKey"] == "reloj"
    assert row["privacyStatus"] == "private" and row["publishAt"] == SLOT_Z and row["title"] == TITLE
    assert ctx["yt"].listed[-1]["part"] == "status,snippet"


def test_list_empty_skips_api(ctx):
    r = ctx["mod"].list_scheduled(ToolRequest(businessKey="el-cuis", payload={}), None)

    assert r["data"]["videos"] == [] and ctx["yt"].listed == []


# --- cancel --------------------------------------------------------------------------------------

def _cancel(ctx, video_id="vid123", approval_id=None):
    return ctx["mod"].cancel_scheduled(_req({"videoId": video_id}, approval_id), None)


def test_cancel_deletes_and_marks_cancelled(ctx, execute, social_profile_dir):
    execute()

    d = _cancel(ctx)
    assert d["approvalId"] and ctx["yt"].deleted == []
    done = _cancel(ctx, approval_id=d["approvalId"])

    assert done["executed"] and ctx["yt"].deleted == [{"id": "vid123"}]
    assert _queue("pending") == [] and len(_queue("cancelled")) == 1
    assert _logged(ctx["s3"])["state"] == "cancelled"


def test_cancel_refuses_after_publish_time(ctx, execute, social_profile_dir, monkeypatch):
    execute()
    monkeypatch.setattr(ctx["mod"], "_now", lambda: datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc))

    assert _cancel(ctx).get("approvalId") is None


def test_cancel_refuses_public_video(ctx, execute, social_profile_dir):
    execute()
    ctx["yt"].videos_by_id["vid123"]["status"]["privacyStatus"] = "public"

    assert _cancel(ctx).get("approvalId") is None


def test_cancel_unknown_video_is_invalid(ctx):
    with pytest.raises(AdsMcpError) as err:
        _cancel(ctx, video_id="nope")
    assert err.value.error_code == "REQUEST_INVALID"


def test_server_registers_schedule_tools():
    server = load_service_module("youtube", "mcp_server")
    for name in ("youtube_schedule_video", "youtube_list_scheduled", "youtube_cancel_scheduled"):
        assert hasattr(server, name)
    assert "approval_id" in server.mcp.instructions


def test_server_passes_tool_arguments(monkeypatch):
    server = load_service_module("youtube", "mcp_server")
    seen = {}
    monkeypatch.setattr(server, "schedule_video", lambda req, rid: seen.update(req=req) or {"ok": True})
    tool = getattr(server.youtube_schedule_video, "fn", server.youtube_schedule_video)

    tool(business_key="el-cuis", file_path="a.mp4", scheduled_time=SLOT, title=TITLE, description="d")

    req = seen["req"]
    assert req.dryRun and req.payload == {"filePath": "a.mp4", "scheduledTime": SLOT, "title": TITLE, "description": "d"}


# --- unknown vs definite upload failures (fix round 1) ---------------------------------------

def _http_error(status):
    from googleapiclient.errors import HttpError

    resp = type("Resp", (), {"status": status, "reason": "err"})()
    return HttpError(resp, b'{"error": {"message": "bad"}}')


def test_upload_passes_num_retries(ctx, execute, social_profile_dir):
    execute()

    assert ctx["yt"].chunk_calls and all(c == {"num_retries": 3} for c in ctx["yt"].chunk_calls)


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionResetError("reset"), "http500"])
def test_unknown_outcome_logs_queued_without_ref_or_verify_entry(ctx, execute, social_profile_dir, error):
    ctx["yt"].chunk_error = _http_error(500) if error == "http500" else error

    r = execute()

    result = r["data"]["result"]
    assert result["outcomeUnknown"] is True and result["videoId"] is None and result["verifyQueueKey"] is None
    assert "outcome unknown" in r["summary"] and "YouTube Studio" in r["summary"]
    assert _logged(ctx["s3"])["state"] == "queued" and _logged(ctx["s3"])["ref"] is None
    assert _queue("pending") == []


def test_unknown_outcome_is_not_reoffered_by_scan(ctx, execute, social_profile_dir):
    from shared import social_scan

    ctx["yt"].chunk_error = TimeoutError("timed out")
    execute()

    profile = {"readyFolder": str(ctx["video"].parent), "baselineCutoff": "2000-01-01T00:00:00Z",
               "platforms": ["youtube"]}
    scan = social_scan.find_new_videos(profile, social_ledger.load("el-cuis"), queue_entries=_queue("pending"))
    assert scan["candidates"] == []


def test_unknown_outcome_listed(ctx, execute, social_profile_dir):
    ctx["yt"].chunk_error = TimeoutError("timed out")
    execute()
    ctx["yt"].listed.clear()

    r = ctx["mod"].list_scheduled(ToolRequest(businessKey="el-cuis", payload={}), None)

    [row] = r["data"]["videos"]
    assert row["outcomeUnknown"] is True and row["videoId"] is None and ctx["yt"].listed == []


def test_http_4xx_on_upload_marks_failed(ctx, execute, social_profile_dir):
    ctx["yt"].chunk_error = _http_error(400)

    with pytest.raises(AdsMcpError) as err:
        execute()

    assert err.value.error_code == "UPSTREAM_ERROR" and _logged(ctx["s3"])["state"] == "failed"
    assert _queue("pending") == []


def test_http_4xx_on_insert_marks_failed(ctx, execute, social_profile_dir, monkeypatch):
    def refused(**kwargs):
        raise _http_error(400)
    monkeypatch.setattr(ctx["yt"], "insert", refused)

    with pytest.raises(AdsMcpError):
        execute()

    assert _logged(ctx["s3"])["state"] == "failed"
