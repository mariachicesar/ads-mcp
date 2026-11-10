#!/usr/bin/env python3
"""Set a client's Google Business Profile account + location IDs in its manifest.

Get the values from scripts/gbp-discover.py, then:
    python scripts/set-gbp-ids.py --key el-cuis --account "accounts/123" --location 456 --dry-run
    python scripts/set-gbp-ids.py --key el-cuis --account "accounts/123" --location 456
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import manifest as manifest_mod  # noqa: E402
from shared.models import PlatformConfig  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--key", required=True)
    p.add_argument("--account", required=True, help='e.g. "accounts/105069442623176324112"')
    p.add_argument("--location", required=True, help="bare location id, e.g. 6251638630856099146")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    location = args.location.split("/")[-1]  # accept "locations/123" too
    account = args.account if args.account.startswith("accounts/") else f"accounts/{args.account}"

    m = manifest_mod.load_client_manifest(args.key)
    if m is None:
        print(f"No manifest found for {args.key}", file=sys.stderr)
        return 1
    pc = m.platforms.get("gbp") or PlatformConfig(enabled=True)
    before = pc.metadata()
    data = pc.model_dump()
    data.update({"enabled": True, "gbp_account_id": account, "gbp_location_id": location})
    m.platforms["gbp"] = PlatformConfig.model_validate(data)
    manifest_mod.upsert_client_manifest(m, dry_run=args.dry_run)
    print(f"{args.key} gbp_account_id: {before.get('gbp_account_id')!r} -> {account!r}")
    print(f"{args.key} gbp_location_id: {before.get('gbp_location_id')!r} -> {location!r}")
    print("(dry run, nothing saved)" if args.dry_run else "(saved to Secrets Manager)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
