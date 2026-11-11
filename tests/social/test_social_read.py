from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared import media_queue, social_ledger
from shared.models import ToolRequest
from tests.fake_s3 import fake_s3  # noqa: F401  (fixture)
from tests.service_import import load_service_module

NOW = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)  # Mon 12:00 PST


@pytest.fixture
def read(monkeypatch, fake_s3, social_profile_dir):
    mod = load_service_module("social", "tools.read")
    monkeypatch.setattr(mod, "_now", lambda: NOW)
    return mod


def _req(payload=None, key="el-cuis"):
    return ToolRequest(businessKey=key, payload=payload or {})


def _slot_times(resp):
    return [s["utc"] for s in resp["data"]["slots"]]


def test_open_slots_without_taken(read):
    resp = read.next_open_slots(_req(), None)
    assert len(resp["data"]["slots"]) == 9


def test_pending_queue_entries_and_ledger_queued_are_taken(read):
    media_queue.enqueue({"businessKey": "el-cuis", "platform": "instagram", "scheduledAt": "2026-11-17T03:00:00Z"})  # Mon 19:00 PST
    media_queue.enqueue({"businessKey": "other", "platform": "tiktok", "scheduledAt": "2026-11-18T03:00:00Z"})
    social_ledger.mark("el-cuis", song_key="a", sha256="s", platform="facebook", state="queued",
                       scheduled_at="2026-11-19T03:00:00Z", ref="r")  # Wed 19:00 PST
    social_ledger.mark("el-cuis", song_key="b", sha256="t", platform="tiktok", state="failed",
                       scheduled_at="2026-11-20T03:00:00Z", ref=None)  # failed: not taken
    times = _slot_times(read.next_open_slots(_req(), None))
    assert "2026-11-17T03:00:00Z" not in times
    assert "2026-11-19T03:00:00Z" not in times
    assert "2026-11-18T03:00:00Z" in times and "2026-11-20T03:00:00Z" in times
    assert len(times) == 7


def test_bad_weekday_time_is_request_invalid(read):
    from shared.errors import AdsMcpError
    with pytest.raises(AdsMcpError) as exc:
        read.next_open_slots(_req({"weekdayTime": "21:30"}), None)
    assert exc.value.error_code == "REQUEST_INVALID"


def test_find_new_videos_returns_scan(read, tmp_path, monkeypatch):
    from shared import social_scan
    monkeypatch.setattr(social_scan, "_created_at", lambda p: NOW.timestamp())
    folder = tmp_path / "ready"
    folder.mkdir()
    (folder / "Old Reel.mp4").write_bytes(b"x")
    resp = read.find_new_videos(_req({"folder": str(folder)}), None)
    assert [c["songKey"] for c in resp["data"]["candidates"]] == ["old"]
    assert resp["data"]["baselineCount"] == 0


def test_get_posting_log(read):
    social_ledger.mark("el-cuis", song_key="a", sha256="s", platform="facebook", state="queued",
                       scheduled_at="2026-11-19T03:00:00Z", ref="r")
    resp = read.get_posting_log(_req(), None)
    assert "a" in resp["data"]["songs"]


@pytest.mark.parametrize("fn", ["find_new_videos", "next_open_slots", "get_posting_log"])
def test_unknown_business_is_request_invalid(read, fn):
    from shared.errors import AdsMcpError
    with pytest.raises(AdsMcpError) as exc:
        getattr(read, fn)(_req(key="nobody"), None)
    assert exc.value.error_code == "REQUEST_INVALID"
    assert "no social profile" in exc.value.message
