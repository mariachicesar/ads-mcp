from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared import scheduling, social_scan

BEFORE = datetime(2026, 11, 1, 12, tzinfo=timezone.utc).timestamp()
AFTER = datetime(2026, 11, 11, 12, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def scan(profile, tmp_path, monkeypatch):
    created: dict[str, float] = {}
    monkeypatch.setattr(social_scan, "_created_at", lambda p: created[p.replace("\\", "/").rsplit("/", 1)[-1]])

    def make(name: str, when: float = AFTER, content: bytes | None = None) -> str:
        path = tmp_path / name
        path.write_bytes(content if content is not None else name.encode())
        created[name] = when
        return str(path)

    def run(ledger: dict | None = None, queue: list | None = None) -> dict:
        extra = {"queue_entries": queue} if queue is not None else {}
        return social_scan.find_new_videos(profile, ledger or {"songs": {}}, folder=str(tmp_path), **extra)

    return make, run


def _logged(sha: str, states: dict[str, str], updated: str = "2026-11-12T00:00:00Z") -> dict:
    return {"songs": {"reloj": {"files": [sha], "platforms": {
        p: {"state": s, "scheduledAt": None, "sha256": sha, "ref": None, "updatedAt": updated} for p, s in states.items()}}}}


def test_files_before_cutoff_are_baseline(scan, monkeypatch):
    make, run = scan
    make("Old Reel.mp4", BEFORE)
    calls = []
    monkeypatch.setattr(scheduling, "sha256_file", lambda p: calls.append(p) or "x")
    result = run()
    assert result["candidates"] == [] and result["baselineCount"] == 1
    assert calls == []


def test_created_before_cutoff_modified_after_is_baseline(scan):
    import os
    make, run = scan
    path = make("Old Reel.mp4", BEFORE)
    os.utime(path, (AFTER, AFTER))
    assert run()["baselineCount"] == 1


def test_reel_preferred_over_post(scan):
    make, run = scan
    make("X Post.mp4", AFTER)
    make("X Reel.mp4", AFTER + 5)
    result = run()
    assert [c["cut"] for c in result["candidates"]] == ["reel"]
    assert result["skipped"] == [{"file": result["skipped"][0]["file"], "reason": "other cut of the same song"}]
    assert result["skipped"][0]["file"].endswith("X Post.mp4")


def test_only_post_is_post_cut(scan):
    make, run = scan
    make("X Post.mp4")
    assert [c["cut"] for c in run()["candidates"]] == ["post"]


def test_song_done_on_all_platforms_is_skipped(scan):
    make, run = scan
    path = make("Reloj Reel.mp4")
    sha = scheduling.sha256_file(path)
    result = run(_logged(sha, {"facebook": "queued", "instagram": "queued", "tiktok": "queued"}))
    assert result["candidates"] == [] and result["skipped"] == []


def test_partial_song_lists_missing_platforms(scan):
    make, run = scan
    path = make("Reloj Reel.mp4")
    sha = scheduling.sha256_file(path)
    result = run(_logged(sha, {"facebook": "queued", "instagram": "queued", "tiktok": "failed"}))
    cand = result["candidates"][0]
    assert cand["missingPlatforms"] == ["tiktok"] and cand["possibleRepeat"] is False


def test_new_cut_of_baseline_song_flagged_possible_repeat(scan):
    make, run = scan
    make("Reloj Reel.mp4", BEFORE)
    make("Reloj2 Reel.mp4", AFTER)
    result = run()
    assert result["baselineCount"] == 1
    cand = result["candidates"][0]
    assert cand["possibleRepeat"] is True and cand["missingPlatforms"] == ["facebook", "instagram", "tiktok"]


def test_sibling_cut_existing_when_song_scheduled_is_skipped(scan):
    make, run = scan
    scheduled = make("Reloj Reel.mp4", AFTER)
    make("Reloj Post.mp4", AFTER)  # created before the log entry below -> the other cut
    sha = scheduling.sha256_file(scheduled)
    result = run(_logged(sha, {"facebook": "queued", "instagram": "queued", "tiktok": "queued"}))
    assert result["candidates"] == []
    assert [s["reason"] for s in result["skipped"]] == ["other cut of an already scheduled song"]


LATER = datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp()


def test_re_export_after_scheduling_is_possible_repeat(scan):
    make, run = scan
    make("Reloj2 Reel.mp4", LATER)  # the scheduled original has left the folder
    result = run(_logged("gone", {"facebook": "queued", "instagram": "queued", "tiktok": "failed"}))
    cand = result["candidates"][0]
    assert cand["possibleRepeat"] is True and cand["file"].endswith("Reloj2 Reel.mp4")
    assert cand["missingPlatforms"] == ["tiktok"]  # never re-offers a platform already queued for the song


def test_re_export_of_song_queued_everywhere_is_skipped(scan):
    make, run = scan
    scheduled = make("Reloj Reel.mp4", AFTER)
    make("Reloj2 Reel.mp4", LATER)
    sha = scheduling.sha256_file(scheduled)
    result = run(_logged(sha, {"facebook": "queued", "instagram": "queued", "tiktok": "queued"}))
    assert result["candidates"] == []
    assert [s["reason"] for s in result["skipped"]] == ["song already scheduled on every platform"]


def test_newer_reel_does_not_hide_post_cut_retry(scan):
    make, run = scan
    post = make("Reloj Post.mp4", AFTER)
    make("Reloj Reel.mp4", LATER)  # arrives after the Post cut was queued
    sha = scheduling.sha256_file(post)
    result = run(_logged(sha, {"facebook": "queued", "instagram": "queued", "tiktok": "failed"}))
    [cand] = result["candidates"]
    assert cand["file"].endswith("Reloj Post.mp4") and cand["possibleRepeat"] is False
    assert cand["missingPlatforms"] == ["tiktok"]
    assert [(s["file"][-14:], s["reason"]) for s in result["skipped"]] == [
        ("Reloj Reel.mp4", "other cut of the same song")]


def test_pending_queue_entries_count_as_queued(scan):
    make, run = scan
    path = make("Reloj Reel.mp4")
    sha = scheduling.sha256_file(path)
    queue = [{"businessKey": "el-cuis", "platform": "instagram", "videoSha256": sha},
             {"businessKey": "el-cuis", "platform": "tiktok", "videoSha256": "other"}]
    [cand] = run(queue=queue)["candidates"]
    assert cand["missingPlatforms"] == ["facebook", "tiktok"] and cand["possibleRepeat"] is False


def test_queue_entries_fill_gaps_in_ledger(scan):
    make, run = scan
    path = make("Reloj Reel.mp4")
    sha = scheduling.sha256_file(path)
    queue = [{"businessKey": "el-cuis", "platform": "tiktok", "videoSha256": sha}]
    result = run(_logged(sha, {"facebook": "queued", "instagram": "queued"}), queue)
    assert result["candidates"] == [] and result["skipped"] == []


def test_over_300mb_flags_instagram(scan, monkeypatch):
    make, run = scan
    make("Big Reel.mp4")
    make("Small Reel.mp4")
    monkeypatch.setattr(social_scan, "_size", lambda p: 300_000_001 if "Big" in p else 10)
    flags = {("Big" in c["file"]): c["instagramTooLarge"] for c in run()["candidates"]}
    assert flags == {True: True, False: False}


def test_non_video_files_ignored(scan):
    make, run = scan
    make("cover.png")
    make("notes.txt")
    make("Clip Reel.MOV")
    assert [c["file"][-8:] for c in run()["candidates"]] == ["Reel.MOV"]


def test_order_oldest_first(scan):
    make, run = scan
    make("B Reel.mp4", AFTER + 100)
    make("A Reel.mp4", AFTER + 200)
    make("C Reel.mp4", AFTER)
    assert [c["songKey"] for c in run()["candidates"]] == ["c", "b", "a"]
