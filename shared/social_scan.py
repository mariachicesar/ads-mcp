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


def find_new_videos(profile: dict[str, Any], ledger: dict[str, Any], *, folder: str | None = None,
                    queue_entries: list[dict[str, Any]] | tuple = ()) -> dict[str, Any]:
    """Scan the Ready folder.

    queue_entries are the business's pending/processing S3 queue entries; a (videoSha256, platform)
    pair found there counts as queued even if the posting log missed it (a ledgerWarning).
    """
    folder = folder or profile["readyFolder"]
    cutoff = _parse_utc(profile["baselineCutoff"])
    platforms = list(profile["platforms"])
    songs = ledger.get("songs", {})
    # Songs already scheduled when a platform was switched on never get that platform (no backfill).
    excluded = {p: set(v.get("excludeSongs", ())) for p, v in profile.get("platformStarts", {}).items()}
    sha_to_song = {sha: key for key, song in songs.items() for sha in song.get("files", [])}
    queued_in_queue: dict[str, set[str]] = {}
    for entry in queue_entries:
        if entry.get("videoSha256") and entry.get("platform"):
            queued_in_queue.setdefault(entry["videoSha256"], set()).add(entry["platform"])

    def queued_for(key: str, sha: str) -> set[str]:
        song = songs.get(key, {})
        queued = {p for p, rec in song.get("platforms", {}).items() if rec.get("state") == "queued"}
        for file_sha in {sha, *song.get("files", [])}:
            queued |= queued_in_queue.get(file_sha, set())
        return queued

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
        key = sha_to_song.get(sha, social_ledger.song_key(name))
        # A file already logged or queued by hash is this song's own post, so its gaps are retries.
        matched = sha in sha_to_song or sha in queued_in_queue
        # Platforms already queued for the song are never offered again, whichever cut queued them.
        missing = [p for p in platforms if p not in queued_for(key, sha) and key not in excluded.get(p, ())]
        if matched:
            if not missing:
                continue
            repeat = False
        else:
            repeat = key in baseline_keys or key in songs
            first_logged = _earliest_update(songs[key]) if key in songs else None
            if first_logged and created < first_logged:
                skipped.append({"file": path, "reason": "other cut of an already scheduled song"})
                continue
            if not missing:
                skipped.append({"file": path, "reason": "song already scheduled on every platform"})
                continue
        size = _size(path)
        candidates.append({
            "songKey": key, "file": path, "cut": _cut(name), "createdAt": created.isoformat(), "bytes": size,
            "sha256": sha, "missingPlatforms": missing, "instagramTooLarge": size > INSTAGRAM_MAX_BYTES,
            "possibleRepeat": repeat, "_created": created, "_matched": matched,
        })

    by_song: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        by_song.setdefault(cand["songKey"], []).append(cand)
    chosen: list[dict[str, Any]] = []
    for group in by_song.values():
        # The cut already logged for this song (a retry) beats a new cut; then Reel beats Post.
        group.sort(key=lambda c: (not c["_matched"], c["cut"] != "reel", c["_created"]))
        chosen.append(group[0])
        skipped.extend({"file": c["file"], "reason": "other cut of the same song"} for c in group[1:])
    chosen.sort(key=lambda c: c["_created"])
    for cand in chosen:
        del cand["_created"], cand["_matched"]
    return {"candidates": chosen, "baselineCount": baseline_count, "skipped": skipped}
