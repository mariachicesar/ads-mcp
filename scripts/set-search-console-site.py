#!/usr/bin/env python3
"""Point a client's Search Console config at a different property.

Example (domain property, covers www + non-www + http/https):
    python scripts/set-search-console-site.py --key el-cuis --site "sc-domain:mariachielcuis.com" --dry-run
    python scripts/set-search-console-site.py --key el-cuis --site "sc-domain:mariachielcuis.com"
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
    p.add_argument("--site", required=True, help='e.g. "sc-domain:example.com"')
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    m = manifest_mod.load_client_manifest(args.key)
    if m is None:
        print(f"No manifest found for {args.key}", file=sys.stderr)
        return 1
    pc = m.platforms.get("search-console") or PlatformConfig(enabled=True)
    old = pc.metadata().get("site_url")
    data = pc.model_dump()
    data["site_url"] = args.site
    data["enabled"] = True
    m.platforms["search-console"] = PlatformConfig.model_validate(data)
    manifest_mod.upsert_client_manifest(m, dry_run=args.dry_run)
    print(f"{args.key} search-console site_url: {old!r} -> {args.site!r}"
          + ("  (dry run, nothing saved)" if args.dry_run else "  (saved to Secrets Manager)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
