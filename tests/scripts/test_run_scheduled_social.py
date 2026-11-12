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


def test_bad_entry_does_not_block_other_posts(fake_s3):
    fake_s3.objects["queue/pending/20261112T2000Z-broken.json"] = b"{not json"
    _enqueue(NOW - timedelta(minutes=5))
    pub = Publisher()

    counts = runner.run_once(now=NOW, publishers={"instagram": pub})

    assert counts["published"] == 1 and len(pub.calls) == 1
    assert counts["failed"] == 1
    assert "queue/pending/20261112T2000Z-broken.json" not in fake_s3.objects


def test_last_error_uses_graph_reason(fake_s3):
    _enqueue(NOW - timedelta(minutes=5))
    pub = Publisher(AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message="Meta Graph API rejected POST 178/media.",
                                details={"reason": "(#10) Application does not have permission", "userMessage": "Missing permission"}))

    runner.run_once(now=NOW, publishers={"instagram": pub})

    assert _states(fake_s3)["failed"][0]["lastError"] == "Missing permission"


def test_non_json_result_is_serialized(fake_s3):
    _enqueue(NOW - timedelta(minutes=5))
    pub = Publisher({"mediaId": "m1", "at": NOW})

    counts = runner.run_once(now=NOW, publishers={"instagram": pub})

    assert counts["published"] == 1
    assert _states(fake_s3)["done"][0]["result"]["at"].startswith("2026-11-12")


# ── TikTok publisher ─────────────────────────────────────────────────────────

from tests.fake_tiktok import fake_tt  # noqa: E402,F401  (fixture)

TT_SECRET = "/ads-mcp/el-cuis/tiktok/config"


@pytest.fixture
def tt_ready(fake_s3, fake_tt, fake_secrets):
    fake_secrets.store[TT_SECRET] = {"client_key": "ck", "client_secret": "cs", "refresh_token": "rft.old",
                                     "open_id": "oid", "refresh_expires_at": "2027-11-10T00:00:00Z"}
    fake_s3.objects["videos/el-cuis/t.mp4"] = b"tiktok-video-bytes"
    return fake_tt


def _enqueue_tiktok(when: datetime) -> str:
    return media_queue.enqueue({"platform": "tiktok", "businessKey": "el-cuis",
                                "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "videoKey": "videos/el-cuis/t.mp4",
                                "videoSha256": "t", "caption": "Serenata 🎺 #mariachi", "options": {}})


def test_inbox_status_marks_done_with_note(fake_s3, tt_ready):
    _enqueue_tiktok(NOW - timedelta(minutes=5))

    counts = runner.run_once(now=NOW, publishers={"tiktok": runner.publish_tiktok_entry})

    done = _states(fake_s3)["done"][0]
    assert counts["published"] == 1
    assert done["result"]["publishId"] == "v_inbox_file~v2.123" and done["result"]["status"] == "SEND_TO_USER_INBOX"
    assert "inbox" in done["result"]["note"] and "Serenata" in done["result"]["note"]
    put = next(c for c in tt_ready.calls if c["method"] == "PUT")
    assert put["len"] == len(b"tiktok-video-bytes")
    assert put["headers"]["Content-Range"] == f"bytes 0-{len(b'tiktok-video-bytes') - 1}/{len(b'tiktok-video-bytes')}"


def test_tiktok_failed_status_fails_entry(fake_s3, tt_ready):
    tt_ready.statuses, tt_ready.fail_reason = ["FAILED"], "duration_check_failed"
    _enqueue_tiktok(NOW - timedelta(minutes=5))

    counts = runner.run_once(now=NOW, publishers={"tiktok": runner.publish_tiktok_entry})

    assert counts["failed"] == 1
    assert "duration_check_failed" in _states(fake_s3)["failed"][0]["lastError"]


def test_tiktok_refresh_writeback_failure_requeues(fake_s3, tt_ready, monkeypatch):
    from shared import secrets

    def boom(*a, **kw):
        raise RuntimeError("down")
    monkeypatch.setattr(secrets, "update_secret", boom)
    _enqueue_tiktok(NOW - timedelta(minutes=5))

    runner.run_once(now=NOW, publishers={"tiktok": runner.publish_tiktok_entry})

    assert _states(fake_s3)["pending"][0]["attempts"] == 1
    assert not any(c["url"].endswith("/inbox/video/init/") for c in tt_ready.calls)


def test_tiktok_not_connected_fails_with_hint(fake_s3, fake_tt):
    fake_s3.objects["videos/el-cuis/t.mp4"] = b"x"
    _enqueue_tiktok(NOW - timedelta(minutes=5))

    runner.run_once(now=NOW, publishers={"tiktok": runner.publish_tiktok_entry})

    assert "tiktok-auth.py" in _states(fake_s3)["failed"][0]["lastError"]


def test_main_registers_tiktok_publisher(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "run_once", lambda *, now, publishers: seen.update(publishers) or
                        {"published": 0, "retried": 0, "failed": 0, "skipped": 0})

    assert runner.main() == 0
    assert seen["tiktok"] is runner.publish_tiktok_entry


def test_stale_tiktok_processing_names_tiktok(fake_s3):
    key = _enqueue_tiktok(NOW - timedelta(hours=3))
    media_queue.move(key, "processing", updates={"claimedAt": (NOW - timedelta(minutes=61)).strftime("%Y-%m-%dT%H:%M:%SZ")})

    runner.run_once(now=NOW, publishers={})

    assert _states(fake_s3)["failed"][0]["lastError"] == "stale processing; check the TikTok inbox before retrying"


# ── posting log (spec 7b) ───────────────────────────────────────────────────

from shared import social_ledger  # noqa: E402


def _entry_id(key: str) -> str:
    return key.rsplit("-", 1)[-1].removesuffix(".json")


def _log_queued(ref: str, platform: str = "instagram") -> None:
    social_ledger.mark("el-cuis", song_key="reloj", sha256="x", platform=platform, state="queued",
                       scheduled_at="2026-11-12T20:20:00Z", ref=ref)


def _logged(platform: str = "instagram") -> dict:
    return social_ledger.load("el-cuis")["songs"]["reloj"]["platforms"][platform]


def _hard_fail():
    return AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message="bad video", retryable=False)


def test_permanent_failure_marks_ledger_failed(fake_s3, social_profile_dir):
    entry_id = _entry_id(_enqueue(NOW - timedelta(minutes=5)))
    _log_queued(entry_id)

    runner.run_once(now=NOW, publishers={"instagram": Publisher(_hard_fail())})

    assert (_logged()["state"], _logged()["ref"]) == ("failed", entry_id)


def test_missed_post_marks_ledger_failed(fake_s3, social_profile_dir):
    entry_id = _entry_id(_enqueue(NOW - timedelta(hours=25)))
    _log_queued(entry_id)

    runner.run_once(now=NOW, publishers={"instagram": Publisher()})

    assert _logged()["state"] == "failed"


def test_retry_does_not_mark_ledger(fake_s3, social_profile_dir):
    entry_id = _entry_id(_enqueue(NOW - timedelta(minutes=5)))
    _log_queued(entry_id)

    runner.run_once(now=NOW, publishers={"instagram": Publisher(_transient())})

    assert _logged()["state"] == "queued"


def test_failure_keeps_ledger_record_of_another_entry(fake_s3, social_profile_dir):
    _enqueue(NOW - timedelta(minutes=5))
    _log_queued("newer-entry")

    runner.run_once(now=NOW, publishers={"instagram": Publisher(_hard_fail())})

    assert (_logged()["state"], _logged()["ref"]) == ("queued", "newer-entry")


def test_failure_without_profile_or_logged_song_writes_no_ledger(fake_s3, social_profile_dir, monkeypatch):
    _enqueue(NOW - timedelta(minutes=5))  # no song holds sha "x"
    runner.run_once(now=NOW, publishers={"instagram": Publisher(_hard_fail())})
    assert not any(k.startswith("ledger/") for k in fake_s3.objects)

    monkeypatch.setattr(runner, "load_profile", lambda key: None)
    entry_id = _entry_id(_enqueue(NOW - timedelta(minutes=5)))
    _log_queued(entry_id)
    runner.run_once(now=NOW, publishers={"instagram": Publisher(_hard_fail())})
    assert _logged()["state"] == "queued"


def test_ledger_error_never_affects_publishing(fake_s3, social_profile_dir, monkeypatch):
    _enqueue(NOW - timedelta(minutes=5))
    _enqueue(NOW - timedelta(minutes=4))

    def broken(*a, **kw):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(social_ledger, "song_for_sha", broken)
    counts = runner.run_once(now=NOW, publishers={"instagram": Publisher(_hard_fail(), {"mediaId": "m2"})})

    states = _states(fake_s3)
    assert counts["failed"] == 1 and counts["published"] == 1
    assert states["failed"][0]["lastError"] == "bad video" and len(states["done"]) == 1


# Unknown outcome: the post may already be live, so the log keeps "queued" and the
# next scan never re-offers it (no double post). The queue still shows the failure.

def test_stale_processing_keeps_ledger_queued(fake_s3, social_profile_dir):
    key = _enqueue(NOW - timedelta(minutes=90))
    entry_id = _entry_id(key)
    _log_queued(entry_id)
    media_queue.move(key, "processing", updates={"claimedAt": (NOW - timedelta(minutes=61)).strftime("%Y-%m-%dT%H:%M:%SZ")})

    runner.run_once(now=NOW, publishers={})

    assert len(_states(fake_s3)["failed"]) == 1
    assert (_logged()["state"], _logged()["ref"]) == ("queued", entry_id)


def test_unknown_exception_keeps_ledger_queued(fake_s3, social_profile_dir):
    entry_id = _entry_id(_enqueue(NOW - timedelta(minutes=5)))
    _log_queued(entry_id)

    runner.run_once(now=NOW, publishers={"instagram": Publisher(RuntimeError("connection reset"))})

    assert len(_states(fake_s3)["failed"]) == 1
    assert _logged()["state"] == "queued"


# ── YouTube verify entries ──────────────────────────────────────────────────

def _enqueue_youtube(when: datetime, **extra) -> str:
    return media_queue.enqueue({"platform": "youtube", "businessKey": "el-cuis",
                                "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "videoSha256": "x",
                                "videoId": "vid1", "publishAt": "2026-11-12T19:30:00Z", **extra})


def _yt_status(monkeypatch, *statuses):
    import shared.youtube_client as yc
    seq = list(statuses)

    def fake(business_key, video_id):
        out = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(yc, "youtube_status", fake)


def _run_yt(now=NOW):
    return runner.run_once(now=now, publishers={"youtube": runner.verify_youtube_entry})


def test_youtube_public_marks_done(fake_s3, social_profile_dir, monkeypatch):
    entry_id = _entry_id(_enqueue_youtube(NOW - timedelta(minutes=5)))
    _log_queued(entry_id, "youtube")
    _yt_status(monkeypatch, {"privacyStatus": "public", "uploadStatus": "processed", "publishAt": None})

    _run_yt()

    done = _states(fake_s3)["done"]
    assert len(done) == 1 and done[0]["result"] == {"videoId": "vid1", "privacyStatus": "public"}
    assert _logged("youtube")["state"] == "queued"


def test_youtube_still_private_retries_then_fails_without_ledger(fake_s3, social_profile_dir, monkeypatch):
    entry_id = _entry_id(_enqueue_youtube(NOW - timedelta(minutes=5)))
    _log_queued(entry_id, "youtube")
    _yt_status(monkeypatch, {"privacyStatus": "private", "uploadStatus": "processed", "publishAt": "x"})

    _run_yt()
    _run_yt()
    assert len(_states(fake_s3).get("pending", [])) == 1
    _run_yt()

    failed = _states(fake_s3)["failed"]
    assert len(failed) == 1
    assert failed[0]["lastError"] == "YouTube video vid1 is not public after its slot; check YouTube Studio"
    assert _logged("youtube")["state"] == "queued"


def test_youtube_video_missing_is_unknown_outcome(fake_s3, social_profile_dir, monkeypatch):
    _log_queued(_entry_id(_enqueue_youtube(NOW - timedelta(minutes=5))), "youtube")
    _yt_status(monkeypatch, None)

    _run_yt()

    assert len(_states(fake_s3)["failed"]) == 1 and "pending" not in _states(fake_s3)
    assert _logged("youtube")["state"] == "queued"


def test_youtube_missing_but_cancelled_in_log_moves_to_cancelled(fake_s3, social_profile_dir, monkeypatch):
    entry_id = _entry_id(_enqueue_youtube(NOW - timedelta(minutes=5)))
    _log_queued(entry_id, "youtube")
    social_ledger.mark_cancelled("el-cuis", sha256="x", platform="youtube", scheduled_at=None, ref=entry_id)
    assert _logged("youtube")["state"] == "cancelled"
    _yt_status(monkeypatch, None)

    counts = _run_yt()

    assert len(_states(fake_s3)["cancelled"]) == 1 and "failed" not in _states(fake_s3)
    assert counts["failed"] == 0


def test_youtube_transient_api_error_retries_then_unknown(fake_s3, social_profile_dir, monkeypatch):
    _log_queued(_entry_id(_enqueue_youtube(NOW - timedelta(minutes=5))), "youtube")
    _yt_status(monkeypatch, OSError("socket timeout"))

    for _ in range(3):
        _run_yt()

    assert len(_states(fake_s3)["failed"]) == 1
    assert _logged("youtube")["state"] == "queued"


def test_main_registers_youtube_publisher(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "run_once", lambda *, now, publishers: seen.update(publishers) or
                        {"published": 0, "retried": 0, "failed": 0, "skipped": 0})
    runner.main()
    assert seen["youtube"] is runner.verify_youtube_entry


def test_stale_youtube_processing_names_youtube_studio(fake_s3):
    key = _enqueue_youtube(NOW - timedelta(hours=3))
    media_queue.move(key, "processing", updates={"claimedAt": (NOW - timedelta(minutes=61)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    runner.run_once(now=NOW, publishers={})
    assert _states(fake_s3)["failed"][0]["lastError"] == "stale processing; check YouTube Studio before retrying"


def test_youtube_missed_verify_keeps_ledger_queued(fake_s3, social_profile_dir):
    _log_queued(_entry_id(_enqueue_youtube(NOW - timedelta(hours=25))), "youtube")

    runner.run_once(now=NOW, publishers={"youtube": Publisher()})

    assert len(_states(fake_s3)["failed"]) == 1
    assert _logged("youtube")["state"] == "queued"


def test_youtube_without_publisher_keeps_ledger_queued(fake_s3, social_profile_dir):
    _log_queued(_entry_id(_enqueue_youtube(NOW - timedelta(minutes=5))), "youtube")

    runner.run_once(now=NOW, publishers={})

    assert len(_states(fake_s3)["failed"]) == 1
    assert _logged("youtube")["state"] == "queued"


# ── GBP posts ───────────────────────────────────────────────────────────────

WEBSITE_LINE = "Más videos y reservaciones: https://mariachielcuis.com/"


def _enqueue_gbp(when: datetime, **extra) -> str:
    return media_queue.enqueue({"platform": "gbp", "businessKey": "el-cuis",
                                "scheduledAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "videoSha256": "x",
                                "imageKey": "images/el-cuis/reloj.jpg", "summary": "Hola mariachi",
                                "songKey": "reloj", **extra})


@pytest.fixture
def gbp_calls(monkeypatch):
    import servers.gbp.gbp_client as gc
    import shared.runtime_config as rc
    calls = {"create": [], "config": []}

    def fake_config(**kw):
        calls["config"].append(kw)
        return {"gbp_location_id": "1"}

    def fake_create(config, **kw):
        calls["create"].append(kw)
        out = calls.get("outcome")
        if isinstance(out, Exception):
            raise out
        return {"resourceName": "accounts/1/locations/1/localPosts/9", "searchUrl": "https://g.page/p/9"}

    monkeypatch.setattr(rc, "load_platform_runtime_config", fake_config)
    monkeypatch.setattr(gc, "create_post", fake_create)
    monkeypatch.setattr(media_queue, "presigned_get", lambda key, **kw: f"https://s3.test/{key}")
    return calls


def _log_youtube(video_id: str = "vid1", state: str = "queued") -> None:
    social_ledger.mark("el-cuis", song_key="reloj", sha256="x", platform="youtube", state=state,
                       scheduled_at="2026-11-12T19:30:00Z", ref=video_id)


def _run_gbp():
    return runner.run_once(now=NOW, publishers={"gbp": runner.publish_gbp_entry})


def test_gbp_public_youtube_appends_youtube_link(fake_s3, social_profile_dir, monkeypatch, gbp_calls):
    _log_queued("e", "gbp")
    _log_youtube()
    _enqueue_gbp(NOW - timedelta(minutes=5))
    _yt_status(monkeypatch, {"privacyStatus": "public", "uploadStatus": "processed", "publishAt": None})

    _run_gbp()

    (call,) = gbp_calls["create"]
    assert call["summary"] == "Hola mariachi\n\n▶️ Mira el video completo en YouTube: https://youtu.be/vid1"
    assert call["call_to_action_type"] == "CALL" and call["topic_type"] == "STANDARD"
    assert call["media_url"] == "https://s3.test/images/el-cuis/reloj.jpg" and call["language_code"] == "es"
    assert gbp_calls["config"][0]["platform"] == "gbp" and gbp_calls["config"][0]["business_key"] == "el-cuis"
    (done,) = _states(fake_s3)["done"]
    assert done["result"]["resourceName"].endswith("localPosts/9")
    assert done["result"]["searchUrl"] == "https://g.page/p/9" and "youtu.be/vid1" in done["result"]["linkLine"]


@pytest.mark.parametrize("setup", ["private", "no_record", "lookup_raises", "no_status", "not_queued"])
def test_gbp_falls_back_to_website_line(fake_s3, social_profile_dir, monkeypatch, gbp_calls, setup):
    _log_queued("e", "gbp")
    if setup != "no_record":
        _log_youtube(state="failed" if setup == "not_queued" else "queued")
    status = {"private": {"privacyStatus": "private"}, "no_status": None,
              "lookup_raises": RuntimeError("boom")}.get(setup, {"privacyStatus": "public"})
    _yt_status(monkeypatch, status)
    _enqueue_gbp(NOW - timedelta(minutes=5))

    _run_gbp()

    assert gbp_calls["create"][0]["summary"] == "Hola mariachi\n\n" + WEBSITE_LINE
    assert len(_states(fake_s3)["done"]) == 1


def test_gbp_4xx_fails_entry_and_ledger(fake_s3, social_profile_dir, monkeypatch, gbp_calls):
    entry_id = _entry_id(_enqueue_gbp(NOW - timedelta(minutes=5)))
    _log_queued(entry_id, "gbp")
    _yt_status(monkeypatch, None)
    gbp_calls["outcome"] = AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message="GBP v4 API error: 400",
                                       retryable=False)

    _run_gbp()

    assert len(_states(fake_s3)["failed"]) == 1
    assert (_logged("gbp")["state"], _logged("gbp")["ref"]) == ("failed", entry_id)


def test_gbp_5xx_is_unknown_outcome_not_retried(fake_s3, social_profile_dir, monkeypatch, gbp_calls):
    entry_id = _entry_id(_enqueue_gbp(NOW - timedelta(minutes=5)))
    _log_queued(entry_id, "gbp")
    _yt_status(monkeypatch, None)
    gbp_calls["outcome"] = _transient()

    counts = _run_gbp()

    assert counts["retried"] == 0 and len(gbp_calls["create"]) == 1
    (failed,) = _states(fake_s3)["failed"]
    assert "check the El Cuis Business Profile posts" in failed["lastError"]
    assert _logged("gbp")["state"] == "queued"


def test_gbp_requires_account_id_and_config_error_precedes_post(fake_s3, social_profile_dir, monkeypatch, gbp_calls):
    import shared.runtime_config as rc

    def missing(**kw):
        gbp_calls["config"].append(kw)
        raise AdsMcpError(status_code=500, error_code="INTERNAL_ERROR", message="missing gbp_account_id")

    monkeypatch.setattr(rc, "load_platform_runtime_config", missing)
    _enqueue_gbp(NOW - timedelta(minutes=5))

    _run_gbp()

    assert "gbp_account_id" in gbp_calls["config"][0]["required_keys"]
    assert gbp_calls["create"] == [] and len(_states(fake_s3)["failed"]) == 1


def test_gbp_timeout_is_unknown_outcome_ledger_stays_queued(fake_s3, social_profile_dir, monkeypatch, gbp_calls):
    entry_id = _entry_id(_enqueue_gbp(NOW - timedelta(minutes=5)))
    _log_queued(entry_id, "gbp")
    _yt_status(monkeypatch, None)
    gbp_calls["outcome"] = TimeoutError("read timed out")

    _run_gbp()

    assert len(_states(fake_s3)["failed"]) == 1 and _logged("gbp")["state"] == "queued"


def test_main_registers_gbp_publisher(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "run_once", lambda *, now, publishers: seen.update(publishers) or
                        {"published": 0, "retried": 0, "failed": 0, "skipped": 0})
    runner.main()
    assert seen["gbp"] is runner.publish_gbp_entry
