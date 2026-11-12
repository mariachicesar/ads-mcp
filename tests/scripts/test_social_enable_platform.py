from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shared import media_queue, social_ledger
from tests.fake_s3 import fake_s3  # noqa: F401  (fixture)

_SPEC = importlib.util.spec_from_file_location(
    "social_enable_platform", Path(__file__).resolve().parents[2] / "scripts" / "social-enable-platform.py")
enable = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(enable)

NOW = datetime(2026, 11, 12, 20, 30, tzinfo=timezone.utc)


def _seed():
    for song, sha in (("grito", "sha-g"), ("reloj", "sha-r")):
        social_ledger.mark("el-cuis", song_key=song, sha256=sha, platform="facebook", state="queued",
                           scheduled_at=None, ref="r")
    media_queue.enqueue({"platform": "tiktok", "businessKey": "el-cuis", "scheduledAt": "2026-11-20T00:00:00Z",
                         "videoKey": "videos/el-cuis/x.mp4", "videoSha256": "unknown-sha", "caption": "hi"})
    media_queue.enqueue({"platform": "tiktok", "businessKey": "el-cuis", "scheduledAt": "2026-11-20T00:00:00Z",
                         "videoKey": "videos/el-cuis/y.mp4", "videoSha256": "sha-g", "caption": "hi"})


def test_enable_platform_snapshots_ledger_and_pending_queue(fake_s3, social_profile_dir):
    _seed()
    path = social_profile_dir / "el-cuis.json"
    updated = enable.enable_platform(path, "el-cuis", "youtube", now=NOW)
    assert updated["platformStarts"]["youtube"]["excludeSongs"] == ["grito", "reloj"]
    assert updated["platformStarts"]["youtube"]["at"] == "2026-11-12T20:30:00Z"
    assert "youtube" in updated["platforms"]
    assert json.loads(path.read_text(encoding="utf-8")) == updated
    assert len(enable.LAST_UNRESOLVED) == 1 and enable.LAST_UNRESOLVED[0]["videoSha256"] == "unknown-sha"


def test_enable_platform_dry_run_does_not_write(fake_s3, social_profile_dir):
    _seed()
    path = social_profile_dir / "el-cuis.json"
    before = path.read_text(encoding="utf-8")
    updated = enable.enable_platform(path, "el-cuis", "youtube", now=NOW, dry_run=True)
    assert "youtube" in updated["platformStarts"]
    assert path.read_text(encoding="utf-8") == before


def test_enable_platform_refuses_twice(fake_s3, social_profile_dir):
    path = social_profile_dir / "el-cuis.json"
    enable.enable_platform(path, "el-cuis", "youtube", now=NOW)
    with pytest.raises(SystemExit):
        enable.enable_platform(path, "el-cuis", "youtube", now=NOW)
