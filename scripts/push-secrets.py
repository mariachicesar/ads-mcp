#!/usr/bin/env python3
"""Push all business credentials from local-dev-config.json to AWS Secrets Manager.

Usage:
    # Ensure valid AWS credentials first:
    #   export AWS_ACCESS_KEY_ID=...
    #   export AWS_SECRET_ACCESS_KEY=...
    # Or configure ~/.aws/credentials with a valid key.

    python scripts/push-secrets.py [--dry-run]

Secret paths created:
    /ads-mcp/rnr-electrician/google-ads/config
    /ads-mcp/gq-painting/google-ads/config
    /ads-mcp/rnr-electrician/analytics/config      (if ga4_property_id set)
    /ads-mcp/gq-painting/analytics/config          (if ga4_property_id set)
    /ads-mcp/rnr-electrician/search-console/config (if site_url set)
    /ads-mcp/gq-painting/search-console/config     (if site_url set)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# When run from the repo root the config is two levels up; when run as a
# standalone script (e.g. copied to EC2 home dir) fall back to same directory.
_repo_config = ROOT / "local-dev-config.json"
_local_config = Path(__file__).resolve().parent / "local-dev-config.json"
CONFIG_FILE = _repo_config if _repo_config.exists() else _local_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Push credentials to AWS Secrets Manager")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without actually writing")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from scripts import _onboard_core as core

    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found. Create it from local-dev-config.json.", file=sys.stderr)
        sys.exit(1)

    config: dict = json.loads(CONFIG_FILE.read_text())
    for business_key, creds in config.items():
        if not isinstance(creds, dict):
            continue  # skip top-level non-business entries (e.g. anthropic_api_key)
        print(f"Business: {business_key}")
        inputs = core.derive_platform_inputs_from_flat(creds)
        manifest = core.build_manifest(
            business_key,
            creds.get("display_name", business_key),
            platform_inputs=inputs,
        )
        result = core.apply_onboarding(manifest, inputs, dry_run=args.dry_run)
        for pi in inputs:
            state = "enabled" if pi.enabled else "SKIP (not configured)"
            print(f"  {pi.platform}: {state}")
        for sid in result["secretsWritten"]:
            print(f"    {'[DRY RUN] would write' if args.dry_run else 'wrote'}: {sid}")
        print()
    print("Done.")


if __name__ == "__main__":
    main()
