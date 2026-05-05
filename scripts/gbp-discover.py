"""
Discover GBP account IDs and location IDs for all businesses.

Requires a refresh token with the business.manage scope.
Run get-refresh-token.py first if you haven't already.

Usage:
    GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... GOOGLE_REFRESH_TOKEN=... python scripts/gbp-discover.py

Or set them in .env and the script will load it.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env if present
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Load local-dev-config for credentials
dev_config_path = ROOT / "local-dev-config.json"
dev_config = json.loads(dev_config_path.read_text()) if dev_config_path.exists() else {}

# Prefer env vars, fall back to first business config in dev config
def _pick(env_key: str, config_key: str) -> str | None:
    val = os.environ.get(env_key)
    if val:
        return val
    for biz_config in dev_config.values():
        if isinstance(biz_config, dict) and biz_config.get(config_key):
            return biz_config[config_key]
    return None

client_id = _pick("GOOGLE_CLIENT_ID", "client_id")
client_secret = _pick("GOOGLE_CLIENT_SECRET", "client_secret")
refresh_token = _pick("GOOGLE_REFRESH_TOKEN", "refresh_token")

if not all([client_id, client_secret, refresh_token]):
    print("ERROR: Need GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN")
    sys.exit(1)

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

creds = Credentials(
    token=None,
    refresh_token=refresh_token,
    client_id=client_id,
    client_secret=client_secret,
    token_uri="https://oauth2.googleapis.com/token",
    scopes=["https://www.googleapis.com/auth/business.manage"],
)

# List all accounts
account_service = build("mybusinessaccountmanagement", "v1", credentials=creds, cache_discovery=False)
accounts_resp = account_service.accounts().list().execute()
accounts = accounts_resp.get("accounts", [])

if not accounts:
    print("No GBP accounts found for this token.")
    sys.exit(0)

print(f"\nFound {len(accounts)} account(s):\n")
for acct in accounts:
    acct_name = acct.get("name", "")          # e.g. "accounts/123456789"
    acct_display = acct.get("accountName", "")
    acct_type = acct.get("type", "")
    print(f"  Account: {acct_display}")
    print(f"    name (use as gbp_account_id): {acct_name}")
    print(f"    type: {acct_type}")

    # List locations under this account
    loc_service = build("mybusinessbusinessinformation", "v1", credentials=creds, cache_discovery=False)
    try:
        locs_resp = loc_service.accounts().locations().list(
            parent=acct_name,
            readMask="name,title,storeCode",
        ).execute()
        locations = locs_resp.get("locations", [])
        if locations:
            print(f"    Locations ({len(locations)}):")
            for loc in locations:
                loc_name = loc.get("name", "")     # e.g. "accounts/123/locations/456"
                loc_id = loc_name.split("/")[-1] if loc_name else ""
                print(f"      - {loc.get('title', '')}")
                print(f"          name:            {loc_name}")
                print(f"          location_id:     {loc_id}   <-- use as gbp_location_id")
                print(f"          storeCode:       {loc.get('storeCode', '')}")
        else:
            print("    No locations found under this account.")
    except Exception as e:
        print(f"    Could not list locations: {e}")
    print()

print("Copy the appropriate account_id and location_id values into local-dev-config.json")
