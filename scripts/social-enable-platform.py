"""Switch a social platform on for a business without backfilling old songs.

Snapshots every song already scheduled (posting-log songs plus songs behind pending/processing
queue entries) into profile.platformStarts[<platform>].excludeSongs, so the scan never offers
those songs to the new platform, even if they later fail, are cancelled or are re-exported.

    python scripts/social-enable-platform.py --business-key el-cuis --platform youtube [--dry-run]

Queue entries carry no file name; one whose sha is in no posting-log song is reported as
unresolved and is NOT added to the snapshot.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import media_queue, social_ledger  # noqa: E402
from shared.social_profile import _profiles_dir  # noqa: E402

LAST_UNRESOLVED: list[dict] = []


def enable_platform(profile_path: Path, business_key: str, platform: str, *, now: datetime,
                    dry_run: bool = False) -> dict:
    if platform not in social_ledger.PLATFORMS:
        raise SystemExit(f"Unknown platform {platform!r}.")
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    if platform in profile.get("platformStarts", {}):
        raise SystemExit(f"{platform} already has a start snapshot; refusing to overwrite it.")
    ledger = social_ledger.load(business_key)
    songs = set(ledger["songs"])
    sha_to_song = {sha: key for key, song in ledger["songs"].items() for sha in song.get("files", [])}
    unresolved: list[dict] = []
    for entry in media_queue.list_entries(states=("pending", "processing"), business_key=business_key):
        key = sha_to_song.get(entry.get("videoSha256"))
        if key:
            songs.add(key)
        else:
            unresolved.append({"id": entry.get("id"), "videoSha256": entry.get("videoSha256"),
                               "platform": entry.get("platform")})
    LAST_UNRESOLVED[:] = unresolved
    if platform not in profile["platforms"]:
        profile["platforms"] = [*profile["platforms"], platform]
    profile.setdefault("platformStarts", {})[platform] = {
        "at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "excludeSongs": sorted(songs)}
    if not dry_run:
        Path(profile_path).write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return profile


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--business-key", required=True)
    ap.add_argument("--platform", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    path = _profiles_dir() / f"{args.business_key}.json"
    updated = enable_platform(path, args.business_key, args.platform, now=datetime.now(timezone.utc),
                              dry_run=args.dry_run)
    print(json.dumps({"dryRun": args.dry_run, "platformStarts": {args.platform: updated["platformStarts"][args.platform]},
                      "unresolvedQueueEntries": LAST_UNRESOLVED}, indent=2, ensure_ascii=False))
    if LAST_UNRESOLVED:
        print(f"WARNING: {len(LAST_UNRESOLVED)} pending queue entr(y/ies) matched no posting-log song and were "
              "not added; review them.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
