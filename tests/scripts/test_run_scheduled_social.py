from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared import media_queue
from shared.errors import AdsMcpError
from tests.fake_s3 import fake_s3  # noqa: F401  (fixture)

_SPEC = importlib.util.spec_from_file_location(
    "run_scheduled_social", Path(__file__).resolve().parents[2] / "scripts" / "run-scheduled-social.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

NOW = datetime(2026, 11, 12, 20, 30, tzinfo=timezone.utc)


def _enqueue(when: datetime, **extra) -> str:
    return media_queue.enqueue({"platform": "instagram", "businessKey": "el-cuis",
                                "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "videoKey": "videos/el-cuis/x.mp4",
                                "videoSha256": "x", "caption": "hola", "options": {"shareToFeed": True},
                                "igUserId": "178", **extra})


def _states(s3) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for key, body in s3.objects.items():
        if key.startswith("queue/"):
            out.setdefault(key.split("/")[1], []).append(json.loads(body))
    return out


class Publisher:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, entry):
        self.calls.append(entry["id"])
        outcome = self.outcomes.pop(0) if self.outcomes else {"mediaId": "m1"}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _transient():
    return AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message="try later", retryable=True)


def test_run_once_publishes_due_and_skips_future(fake_s3):
    _enqueue(NOW - timedelta(minutes=10))
    _enqueue(NOW + timedelta(hours=2))
    pub = Publisher()

    counts = runner.run_once(now=NOW, publishers={"instagram": pub})

    states = _states(fake_s3)
    assert counts["published"] == 1 and len(pub.calls) == 1
    assert states["done"][0]["result"] == {"mediaId": "m1"} and states["done"][0]["publishedAt"]
    assert len(states["pending"]) == 1


def test_transient_error_requeues_then_fails_after_3(fake_s3):
    _enqueue(NOW - timedelta(minutes=5))
    pub = Publisher(_transient(), _transient(), _transient())

    for expected_attempts in (1, 2):
        runner.run_once(now=NOW, publishers={"instagram": pub})
        pending = _states(fake_s3)["pending"]
        assert pending[0]["attempts"] == expected_attempts and pending[0]["lastError"] == "try later"
    runner.run_once(now=NOW, publishers={"instagram": pub})

    failed = _states(fake_s3)["failed"]
    assert failed[0]["attempts"] == 3 and "pending" not in _states(fake_s3)


def test_non_retryable_error_fails_immediately(fake_s3):
    _enqueue(NOW - timedelta(minutes=5))
    pub = Publisher(AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message="bad video", retryable=False))

    counts = runner.run_once(now=NOW, publishers={"instagram": pub})

    assert counts["failed"] == 1
    assert _states(fake_s3)["failed"][0]["lastError"] == "bad video"


def test_missed_posts_older_than_24h_fail(fake_s3):
    _enqueue(NOW - timedelta(hours=25))
    pub = Publisher()

    runner.run_once(now=NOW, publishers={"instagram": pub})

    assert pub.calls == []
    assert _states(fake_s3)["failed"][0]["lastError"] == "missed: more than 24h late"


def test_stale_processing_is_failed_not_retried(fake_s3):
    key = _enqueue(NOW - timedelta(hours=3))
    media_queue.move(key, "processing", updates={"claimedAt": (NOW - timedelta(minutes=61)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    pub = Publisher()

    runner.run_once(now=NOW, publishers={"instagram": pub})

    assert pub.calls == []
    assert _states(fake_s3)["failed"][0]["lastError"] == "stale processing; check Instagram before retrying"


def test_recent_processing_is_left_alone(fake_s3):
    key = _enqueue(NOW - timedelta(minutes=20))
    media_queue.move(key, "processing", updates={"claimedAt": (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")})

    runner.run_once(now=NOW, publishers={"instagram": Publisher()})

    assert len(_states(fake_s3)["processing"]) == 1


def test_claim_is_exclusive(fake_s3, monkeypatch):
    _enqueue(NOW - timedelta(minutes=5))
    real_move = media_queue.move

    def lose_claim(key, to_state, **kw):
        if to_state == "processing":
            raise KeyError(key)  # another runner claimed it first
        return real_move(key, to_state, **kw)

    monkeypatch.setattr(runner.media_queue, "move", lose_claim)
    pub = Publisher()

    counts = runner.run_once(now=NOW, publishers={"instagram": pub})

    assert pub.calls == [] and counts["skipped"] == 1


def test_unknown_platform_fails(fake_s3):
    _enqueue(NOW - timedelta(minutes=5), platform="myspace")

    counts = runner.run_once(now=NOW, publishers={"instagram": Publisher()})

    assert counts["failed"] == 1
    assert "no publisher" in _states(fake_s3)["failed"][0]["lastError"]


def test_publish_instagram_entry_uses_presigned_url(fake_s3, monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "load_meta_ads_config", lambda **kw: {"access_token": "tok"})
    monkeypatch.setattr(runner, "publish_reel", lambda config, **kw: seen.update(kw) or {"mediaId": "m9"})

    result = runner.publish_instagram_entry({"businessKey": "el-cuis", "videoKey": "videos/el-cuis/x.mp4",
                                             "caption": "hola", "igUserId": "178", "options": {"shareToFeed": True}})

    assert result == {"mediaId": "m9"}
    assert seen["ig_user_id"] == "178" and seen["video_url"].startswith("https://s3.example/videos/el-cuis/x.mp4")
    assert seen["caption"] == "hola" and seen["share_to_feed"] is True
