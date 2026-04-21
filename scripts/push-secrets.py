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

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# When run from the repo root the config is two levels up; when run as a
# standalone script (e.g. copied to EC2 home dir) fall back to same directory.
_repo_config = ROOT / "local-dev-config.json"
_local_config = Path(__file__).resolve().parent / "local-dev-config.json"
CONFIG_FILE = _repo_config if _repo_config.exists() else _local_config
REGION = "us-east-1"


def aws_put_secret(secret_id: str, value: dict, dry_run: bool) -> None:
    secret_str = json.dumps(value)
    if dry_run:
        print(f"  [DRY RUN] Would write: {secret_id}")
        print(f"            Keys: {list(value.keys())}")
        return

    # Try create first, update if already exists
    result = subprocess.run(
        ["aws", "secretsmanager", "create-secret",
         "--name", secret_id,
         "--secret-string", secret_str,
         "--region", REGION],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  CREATED: {secret_id}")
        return

    if "ResourceExistsException" in result.stderr:
        result2 = subprocess.run(
            ["aws", "secretsmanager", "put-secret-value",
             "--secret-id", secret_id,
             "--secret-string", secret_str,
             "--region", REGION],
            capture_output=True, text=True,
        )
        if result2.returncode == 0:
            print(f"  UPDATED: {secret_id}")
            return
        print(f"  ERROR updating {secret_id}: {result2.stderr[:300]}", file=sys.stderr)
        return

    print(f"  ERROR creating {secret_id}: {result.stderr[:300]}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Push credentials to AWS Secrets Manager")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without actually writing")
    args = parser.parse_args()

    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found. Create it from local-dev-config.json.", file=sys.stderr)
        sys.exit(1)

    config: dict = json.loads(CONFIG_FILE.read_text())

    # Verify AWS auth first
    if not args.dry_run:
        check = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--region", REGION],
            capture_output=True, text=True,
        )
        if check.returncode != 0:
            print("ERROR: AWS credentials are not valid. Fix ~/.aws/credentials first.", file=sys.stderr)
            print(check.stderr[:200], file=sys.stderr)
            sys.exit(1)
        identity = json.loads(check.stdout)
        print(f"AWS identity: {identity.get('Arn')}\n")

    for business_key, creds in config.items():
        print(f"Business: {business_key}")

        # Google Ads config — always push
        ads_secret = {k: v for k, v in creds.items() if k in (
            "customer_account_id", "manager_account_id",
            "developer_token", "client_id", "client_secret", "refresh_token",
        )}
        aws_put_secret(f"/ads-mcp/{business_key}/google-ads/config", ads_secret, args.dry_run)

        # GA4 analytics config — push only if property_id is set
        if creds.get("ga4_property_id"):
            analytics_secret = {
                "property_id": creds["ga4_property_id"],
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": creds["refresh_token"],
            }
            aws_put_secret(f"/ads-mcp/{business_key}/analytics/config", analytics_secret, args.dry_run)
        else:
            print(f"  SKIP analytics — no ga4_property_id in config for {business_key}")

        # Search Console config — push only if site_url is set
        if creds.get("site_url"):
            sc_secret = {
                "site_url": creds["site_url"],
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": creds["refresh_token"],
            }
            aws_put_secret(f"/ads-mcp/{business_key}/search-console/config", sc_secret, args.dry_run)
        else:
            print(f"  SKIP search-console — no site_url in config for {business_key}")

        print()

    print("Done.")


if __name__ == "__main__":
    main()
