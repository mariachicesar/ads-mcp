from __future__ import annotations

import json

import pytest

from shared import social_ledger
from shared.social_ledger import song_key
from tests.fake_s3 import PreconditionFailed, fake_s3  # noqa: F401  (fixture)


@pytest.mark.parametrize("a,b", [
    ("6 pies abajo Post.mp4", "6piesAbajo Reel.mp4"),
    ("17anos Reel.mp4", "17anos Reel (1).mp4"),
    ("Caminos de Guanajuato2 Reel.mp4", "Caminos de Guanajuato3 Reel.mp4"),
    ("Hermoso Carino Post.mp4", "Hermoso Cariño Reel.mov"),
])
def test_same_song(a, b):
    assert song_key(a) == song_key(b)


def test_different_songs():
    assert song_key("17anos Reel.mp4") != song_key("20 Anos Reel.mp4")


def _mark(platform="instagram", sha="aaa", song="17anos", state="queued", ref="e1"):
    social_ledger.mark("el-cuis", song_key=song, sha256=sha, platform=platform, state=state,
                       scheduled_at="2026-11-13T02:00:00Z", ref=ref)


def test_load_empty(fake_s3):
    assert social_ledger.load("el-cuis") == {"songs": {}}


def test_mark_then_load_roundtrip(fake_s3):
    _mark()

    song = social_ledger.load("el-cuis")["songs"]["17anos"]
    assert song["files"] == ["aaa"]
    p = song["platforms"]["instagram"]
    assert (p["state"], p["scheduledAt"], p["sha256"], p["ref"]) == ("queued", "2026-11-13T02:00:00Z", "aaa", "e1")
    assert p["updatedAt"].endswith("Z")
    assert "ledger/el-cuis.json" in fake_s3.objects


def test_mark_keeps_other_platforms(fake_s3):
    _mark("instagram")
    _mark("tiktok", ref="e2")
    _mark("instagram", state="cancelled")

    platforms = social_ledger.load("el-cuis")["songs"]["17anos"]["platforms"]
    assert platforms["tiktok"]["state"] == "queued" and platforms["tiktok"]["ref"] == "e2"
    assert platforms["instagram"]["state"] == "cancelled"


def test_mark_appends_sha_once(fake_s3):
    _mark(sha="aaa")
    _mark("tiktok", sha="aaa")
    _mark("facebook", sha="bbb")

    assert social_ledger.load("el-cuis")["songs"]["17anos"]["files"] == ["aaa", "bbb"]


def test_mark_retries_on_precondition_failure(fake_s3):
    _mark()
    real = fake_s3.put_object
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PreconditionFailed("race")
        return real(**kw)

    fake_s3.put_object = flaky
    _mark("tiktok")

    assert calls["n"] == 3
    assert "tiktok" in social_ledger.load("el-cuis")["songs"]["17anos"]["platforms"]


def test_mark_raises_after_three_failures(fake_s3):
    def always(**kw):
        raise PreconditionFailed("race")

    fake_s3.put_object = always
    with pytest.raises(PreconditionFailed):
        _mark()


def test_mark_cancelled_noop_when_sha_unknown(fake_s3):
    social_ledger.mark_cancelled("el-cuis", sha256="zzz", platform="tiktok", scheduled_at=None, ref="x")

    assert not any(k.startswith("ledger/") for k in fake_s3.objects)


def _platform(platform="instagram"):
    return social_ledger.load("el-cuis")["songs"]["17anos"]["platforms"][platform]


def test_mark_cancelled_skips_record_with_other_ref(fake_s3):
    _mark(ref="live")  # the song was re-queued under a newer entry

    social_ledger.mark_cancelled("el-cuis", sha256="aaa", platform="instagram", scheduled_at=None, ref="old")

    assert (_platform()["state"], _platform()["ref"]) == ("queued", "live")


@pytest.mark.parametrize("stored_ref", ["e1", None, ""])
def test_mark_cancelled_marks_matching_or_empty_ref(fake_s3, stored_ref):
    _mark(ref=stored_ref)

    social_ledger.mark_cancelled("el-cuis", sha256="aaa", platform="instagram", scheduled_at=None, ref="e1")

    assert _platform()["state"] == "cancelled"


def test_failed_mark_never_replaces_queued(fake_s3):
    _mark(ref="live")
    _mark(state="failed", ref=None)

    assert (_platform()["state"], _platform()["ref"]) == ("queued", "live")


def test_failed_mark_replaces_failed_or_cancelled(fake_s3):
    _mark(state="cancelled", ref="old")
    _mark(state="failed", ref=None)

    assert _platform()["state"] == "failed"


def test_expected_ref_failed_replaces_own_queued_record_only(fake_s3):
    _mark(ref="e1")
    social_ledger.mark("el-cuis", song_key="17anos", sha256="aaa", platform="instagram", state="failed",
                       scheduled_at=None, ref="other", expected_ref="other")
    assert _platform()["state"] == "queued"

    social_ledger.mark("el-cuis", song_key="17anos", sha256="aaa", platform="instagram", state="failed",
                       scheduled_at=None, ref="e1", expected_ref="e1")
    assert (_platform()["state"], _platform()["ref"]) == ("failed", "e1")


def _run(call):
    return social_ledger.run_and_mark("el-cuis", enabled=True, file_path="C:/v/17anos Reel.mp4", sha256="aaa",
                                      platform="tiktok", scheduled_at="2026-11-13T02:00:00Z", call=call,
                                      ref_of=lambda r: r["entryId"])


def test_run_and_mark_success_with_ledger_failure_returns_warning(fake_s3, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(social_ledger, "mark", broken)
    result = _run(lambda: {"entryId": "e9"})

    assert result["entryId"] == "e9"
    assert "posting log was not updated" in result["ledgerWarning"] and "s3 down" in result["ledgerWarning"]


def test_run_and_mark_platform_error_survives_failed_mark_error(fake_s3, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("s3 down")

    def platform_call():
        raise ValueError("platform said no")

    monkeypatch.setattr(social_ledger, "mark", broken)
    with pytest.raises(ValueError, match="platform said no"):
        _run(platform_call)
