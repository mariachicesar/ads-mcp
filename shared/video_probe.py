"""Read video dimensions/duration with ffprobe and grab still frames with ffmpeg.

Both tools are optional on the machine: `probe` returns None when ffprobe is
missing or fails (callers treat the format as unknown), while `extract_frame`
raises RuntimeError because a caller that needs a frame cannot go on without it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

TIMEOUT_SECONDS = 60


def probe(path: str) -> dict[str, Any] | None:
    """{"width", "height", "seconds"} of the first video stream as displayed (rotation applied), or None."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height:stream_side_data=rotation:stream_tags=rotate:format=duration",
           "-of", "json", path]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if done.returncode != 0:
            return None
        info = json.loads(done.stdout)
        stream = info["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        seconds = float(info["format"]["duration"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        return None
    rotation = (stream.get("tags") or {}).get("rotate")
    for side in stream.get("side_data_list") or []:
        rotation = side.get("rotation", rotation)
    try:
        if int(float(rotation or 0)) % 180 != 0:
            width, height = height, width
    except (TypeError, ValueError):
        pass
    return {"width": width, "height": height, "seconds": seconds}


def extract_frame(path: str, out_path: str, *, at_seconds: float) -> str:
    """Save one JPEG frame at `at_seconds`, scaled to 1200 px wide; returns out_path."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed or not on PATH.")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-v", "error", "-ss", f"{at_seconds:g}", "-i", path,
           "-frames:v", "1", "-vf", "scale=1200:-2", "-q:v", "3", out_path]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"ffmpeg failed: {exc}") from exc
    if done.returncode != 0 or not os.path.isfile(out_path):
        raise RuntimeError(f"ffmpeg failed: {(done.stderr or '').strip()[:500] or 'no frame written'}")
    return out_path
