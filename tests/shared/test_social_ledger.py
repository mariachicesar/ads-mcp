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
