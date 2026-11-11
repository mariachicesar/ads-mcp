"""Find videos in a business's Ready folder that still need posting.

A file is a candidate only if it was created after the profile's baselineCutoff
(older files are the backlog that existed before the workflow began and are
never hashed). Candidates are matched to the posting log by content hash, then
by song key, so a re-export of a song that was already posted is flagged
possibleRepeat rather than silently treated as new.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from shared import scheduling, social_ledger

VIDEO_EXTENSIONS = (".mp4", ".mov")
INSTAGRAM_MAX_BYTES = 300_000_000  # Instagram's Reels upload limit


def _created_at(path: str) -> float:
    st = os.stat(path)
    return getattr(st, "st_birthtime", None) or st.st_ctime


def _size(path: str) -> int:
    return os.path.getsize(path)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _cut(file_name: str) -> str:
    words = os.path.splitext(file_name)[0].casefold().split()
    return "post" if words and words[-1] == "post" else "reel"


def _earliest_update(song: dict[str, Any]) -> datetime | None:
    stamps = [_parse_utc(p["updatedAt"]) for p in song.get("platforms", {}).values() if p.get("updatedAt")]
    return min(stamps) if stamps else None


def find_new_videos(profile: dict[str, Any], ledger: dict[str, Any], *, folder: str | None = None) -> dict[str, Any]:
    folder = folder or profile["readyFolder"]
    cutoff = _parse_utc(profile["baselineCutoff"])
    platforms = list(profile["platforms"])
    songs = ledger.get("songs", {})
    sha_to_song = {sha: key for key, song in songs.items() for sha in song.get("files", [])}

    baseline_count = 0
    baseline_keys: set[str] = set()
    fresh: list[tuple[str, str, datetime]] = []  # (name, path, created)
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not name.lower().endswith(VIDEO_EXTENSIONS) or not os.path.isfile(path):
            continue
        created = datetime.fromtimestamp(_created_at(path), tz=timezone.utc)
        if created < cutoff:
            baseline_count += 1
            baseline_keys.add(social_ledger.song_key(name))
        else:
            fresh.append((name, path, created))

    skipped: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    for name, path, created in fresh:
        sha = scheduling.sha256_file(path)
        key = social_ledger.song_key(name)
        if sha in sha_to_song:
            key = sha_to_song[sha]
            states = songs[key].get("platforms", {})
            missing = [p for p in platforms if states.get(p, {}).get("state") != "queued"]
            if not missing:
                continue
            repeat = False
        else:
            missing = list(platforms)
            repeat = key in baseline_keys or key in songs
            first_logged = _earliest_update(songs[key]) if key in songs else None
            if first_logged and created < first_logged:
                skipped.append({"file": path, "reason": "other cut of an already scheduled song"})
                continue
        size = _size(path)
        candidates.append({
            "songKey": key, "file": path, "cut": _cut(name), "createdAt": created.isoformat(), "bytes": size,
            "sha256": sha, "missingPlatforms": missing, "instagramTooLarge": size > INSTAGRAM_MAX_BYTES,
            "possibleRepeat": repeat, "_created": created,
        })

    by_song: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        by_song.setdefault(cand["songKey"], []).append(cand)
    chosen: list[dict[str, Any]] = []
    for group in by_song.values():
        group.sort(key=lambda c: (c["cut"] != "reel", c["_created"]))
        chosen.append(group[0])
        skipped.extend({"file": c["file"], "reason": "other cut of the same song"} for c in group[1:])
    chosen.sort(key=lambda c: c["_created"])
    for cand in chosen:
        del cand["_created"]
    return {"candidates": chosen, "baselineCount": baseline_count, "skipped": skipped}
