from __future__ import annotations

import json
import subprocess

import pytest

from shared import video_probe


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _ffprobe_json(width, height, duration, rotation=None):
    stream = {"width": width, "height": height}
    if rotation is not None:
        stream["side_data_list"] = [{"rotation": rotation}]
    return json.dumps({"streams": [stream], "format": {"duration": str(duration)}})


@pytest.fixture
def which(monkeypatch):
    monkeypatch.setattr(video_probe.shutil, "which", lambda name: f"/bin/{name}")


def test_probe_reads_width_height_seconds(which, monkeypatch):
    calls = []
    monkeypatch.setattr(video_probe.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _Done(stdout=_ffprobe_json(1080, 1920, 204.6)))

    assert video_probe.probe("a.mp4") == {"width": 1080, "height": 1920, "seconds": 204.6}
    assert calls[0][0] == "/bin/ffprobe" and calls[0][-1] == "a.mp4"


def test_probe_swaps_for_rotated_video(which, monkeypatch):
    monkeypatch.setattr(video_probe.subprocess, "run",
                        lambda cmd, **kw: _Done(stdout=_ffprobe_json(1920, 1080, 30, rotation=-90)))

    assert video_probe.probe("a.mp4") == {"width": 1080, "height": 1920, "seconds": 30.0}


def test_probe_none_without_ffprobe(monkeypatch):
    monkeypatch.setattr(video_probe.shutil, "which", lambda name: None)

    assert video_probe.probe("a.mp4") is None


@pytest.mark.parametrize("result", [_Done(returncode=1, stderr="bad"), _Done(stdout="not json"),
                                    _Done(stdout=json.dumps({"streams": []}))])
def test_probe_none_on_failure(which, monkeypatch, result):
    monkeypatch.setattr(video_probe.subprocess, "run", lambda cmd, **kw: result)

    assert video_probe.probe("a.mp4") is None


def test_probe_none_on_timeout(which, monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr(video_probe.subprocess, "run", slow)

    assert video_probe.probe("a.mp4") is None


def test_extract_frame_runs_ffmpeg(which, monkeypatch, tmp_path):
    out = tmp_path / "frames" / "f.jpg"
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out.write_bytes(b"jpg")
        return _Done()
    monkeypatch.setattr(video_probe.subprocess, "run", run)

    assert video_probe.extract_frame("a.mp4", str(out), at_seconds=12.5) == str(out)
    cmd = calls[0]
    assert cmd[0] == "/bin/ffmpeg"
    joined = " ".join(cmd)
    assert "-ss 12.5" in joined and "-frames:v 1" in joined and "-vf scale=1200:-2" in joined and "-q:v 3" in joined
    assert cmd.index("-ss") < cmd.index("-i") and cmd[-1] == str(out)


def test_extract_frame_raises_without_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(video_probe.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError):
        video_probe.extract_frame("a.mp4", str(tmp_path / "f.jpg"), at_seconds=1)


def test_extract_frame_raises_on_ffmpeg_error(which, monkeypatch, tmp_path):
    monkeypatch.setattr(video_probe.subprocess, "run", lambda cmd, **kw: _Done(returncode=1, stderr="boom"))

    with pytest.raises(RuntimeError, match="boom"):
        video_probe.extract_frame("a.mp4", str(tmp_path / "f.jpg"), at_seconds=1)
