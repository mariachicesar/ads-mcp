"""Connect a business's Google Business Profile once, for scheduled GBP social posts.

    python scripts/gbp-auth.py --business-key el-cuis [--port 8085]

Uses the same Google OAuth client as the other Google servers (GOOGLE_CLIENT_ID /
GOOGLE_CLIENT_SECRET, or asks). A browser opens: sign in with the Google account
that manages the Business Profile and allow access. The script lists the
account's locations (title + primary phone); pick one by number. The refresh
token and the chosen account/location are saved to /ads-mcp/<business>/gbp/config
in Secrets Manager and never printed.

Before running: in the Google Cloud project, enable "My Business Account
Management API" and "My Business Business Information API", and add the
business.manage scope to the OAuth consent screen.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import secrets  # noqa: E402
from shared.caption_rules import has_phone  # noqa: E402
from shared.config import get_settings  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/business.manage"]
LOCATION_READ_MASK = "name,title,phoneNumbers"


def secret_id(business_key: str) -> str:
    return f"/ads-mcp/{business_key}/gbp/config"


def build_config(client_id: str, client_secret: str, refresh_token: str, account: str, location: str) -> dict:
    """The five gbp config keys; account as accounts/<id>, location as the bare id (gbp_client adds the rest)."""
    account_id = account if account.startswith("accounts/") else f"accounts/{account}"
    return {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token,
            "gbp_account_id": account_id, "gbp_location_id": location.rstrip("/").rsplit("/", 1)[-1]}


def phone_matches(listing_phone: str | None, phone: str) -> bool:
    """True if the listing's primary phone is the profile phone (digits compared)."""
    return bool(listing_phone) and has_phone(listing_phone, phone)


def location_rows(accounts: list[dict[str, Any]], locations: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Flatten accounts → locations into numbered-menu rows."""
    rows = []
    for account in accounts:
        for loc in locations.get(account.get("name", ""), []):
            rows.append({"account": account.get("name"), "accountName": account.get("accountName"),
                         "location": loc.get("name"), "title": loc.get("title"),
                         "phone": (loc.get("phoneNumbers") or {}).get("primaryPhone")})
    return rows


def _fetch_rows(creds) -> list[dict[str, Any]]:
    from googleapiclient.discovery import build

    acct_api = build("mybusinessaccountmanagement", "v1", credentials=creds, cache_discovery=False)
    info_api = build("mybusinessbusinessinformation", "v1", credentials=creds, cache_discovery=False)
    accounts = acct_api.accounts().list().execute().get("accounts", [])
    locations: dict[str, list[dict[str, Any]]] = {}
    for account in accounts:
        found, token = [], None
        while True:
            resp = info_api.accounts().locations().list(
                parent=account["name"], readMask=LOCATION_READ_MASK, pageSize=100, pageToken=token,
            ).execute()
            found += resp.get("locations", [])
            token = resp.get("nextPageToken")
            if not token:
                break
        locations[account["name"]] = found
    return location_rows(accounts, locations)


def _profile_phone(business_key: str) -> str | None:
    from shared.social_profile import load_profile

    return (load_profile(business_key) or {}).get("phone")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--business-key", default="el-cuis")
    parser.add_argument("--port", type=int, default=8085,
                        help="Local callback port; http://localhost:<port>/ must be an authorized redirect URI "
                             "on a Web-type OAuth client (8085 matches get-refresh-token.py)")
    args = parser.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    client_id = os.getenv("GOOGLE_CLIENT_ID") or input("Google OAuth client ID: ").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET") or getpass.getpass("Google OAuth client secret (hidden): ").strip()
    flow = InstalledAppFlow.from_client_config({"installed": {
        "client_id": client_id, "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }}, scopes=SCOPES)
    print(f"Redirect URI used: http://localhost:{args.port}/")
    creds = flow.run_local_server(port=args.port, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        print("Not connected: Google returned no refresh token. Remove the app's access at "
              "myaccount.google.com/permissions and run this again.", file=sys.stderr)
        return 1

    rows = _fetch_rows(creds)
    if not rows:
        print("Not connected: this Google account manages no Business Profile locations.", file=sys.stderr)
        return 1
    for i, row in enumerate(rows, 1):
        print(f"  {i}. {row['title']}  ({row['phone'] or 'no phone'})  [{row['accountName']}]")
    choice = input(f"Pick a location (1-{len(rows)}): ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(rows):
        print("Not connected: no valid location picked.", file=sys.stderr)
        return 1
    row = rows[int(choice) - 1]

    phone = _profile_phone(args.business_key)
    if phone and not phone_matches(row["phone"], phone):
        print(f"WARNING: the listing's primary phone ({row['phone'] or 'none'}) is not the profile phone "
              f"{phone}. The post's Call button dials the listing's phone.")

    sid = secret_id(args.business_key)
    secrets.put_secret(sid, build_config(client_id, client_secret, creds.refresh_token, row["account"], row["location"]),
                       get_settings())
    print(f"Connected {row['title']}. Saved to {sid}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
