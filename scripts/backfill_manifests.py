#!/usr/bin/env python3
"""One-time: derive client manifests from existing credential secrets.

Invoked as:
    python scripts/backfill_manifests.py --dry-run
    python scripts/backfill_manifests.py --from-file local-dev-config.json

For each business key in the flat dev config, derive the platform inputs,
flip each platform's ``enabled`` flag on when a credential secret already
exists in the store, then build and upsert the client manifest. Backfill
never writes credential secrets — only the manifest and the client index
(via ``upsert_client_manifest``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _onboard_core as core  # noqa: E402
from shared import manifest as manifest_mod  # noqa: E402
from shared import secrets as secrets_mod  # noqa: E402
from shared.config import get_settings  # noqa: E402


def _platform_secret_exists(business_key: str, platform: str) -> bool:
    settings = get_settings()
    val = secrets_mod.get_secret(
        manifest_mod.credential_secret_id(business_key, platform), settings
    )
    return isinstance(val, dict) and bool(val)


def backfill_from_flat(flat_config: dict, *, dry_run: bool) -> list[dict]:
    results: list[dict] = []
    for business_key, creds in flat_config.items():
        if not isinstance(creds, dict):
            continue
        inputs = core.derive_platform_inputs_from_flat(creds)
        for pi in inputs:
            if _platform_secret_exists(business_key, pi.platform):
                pi.enabled = True
        existing = manifest_mod.load_client_manifest(business_key)
        manifest = core.build_manifest(
            business_key,
            creds.get("display_name", business_key),
            platform_inputs=inputs,
            existing=existing,
        )
        # Backfill must not overwrite credential secrets — only the manifest + index.
        saved = manifest_mod.upsert_client_manifest(manifest, dry_run=dry_run)
        results.append(
            {
                "businessKey": business_key,
                "enabled": [p for p, c in saved.platforms.items() if c.enabled],
                "dryRun": dry_run,
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-file", default=str(ROOT / "local-dev-config.json"))
    args = parser.parse_args(argv)

    flat = json.loads(Path(args.from_file).read_text())
    for row in backfill_from_flat(flat, dry_run=args.dry_run):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
