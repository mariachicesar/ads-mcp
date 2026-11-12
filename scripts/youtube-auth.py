"""Connect a business's YouTube channel once, for the youtube MCP server (reads + scheduled uploads).

    python scripts/youtube-auth.py [--business-key el-cuis] [--channel-id UC...]

Uses the same Google OAuth client as the other Google servers (GOOGLE_CLIENT_ID /
GOOGLE_CLIENT_SECRET, or asks). A browser opens: sign in with the Google account
that owns the channel and allow access. The refresh token is saved to
/ads-mcp/<business>/youtube/config in Secrets Manager and never printed.

Before running: in the Google Cloud project, enable "YouTube Data API v3" and
"YouTube Analytics API", and add the three scopes below to the OAuth consent screen.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import secrets  # noqa: E402
from shared.config import get_settings  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    # force-ssl (not youtube.upload) because videos.delete — used to cancel a scheduled
    # upload and to clean up rejected uploads — is only granted by this broader scope.
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def build_config(client_id: str, client_secret: str, refresh_token: str, channel_id: str | None) -> dict:
    config = {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}
    if channel_id:
        config["channel_id"] = channel_id
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--business-key", default="el-cuis")
    parser.add_argument("--channel-id", default=None, help="Only if the account manages several channels")
    parser.add_argument("--port", type=int, default=8085,
                        help="Local callback port; http://localhost:<port>/ must be an authorized redirect URI "
                             "on a Web-type OAuth client (8085 matches get-refresh-token.py)")
    args = parser.parse_args()

    # Let the flow finish when Google grants fewer scopes, so we can explain what is missing.
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
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
    granted = set(getattr(creds, "granted_scopes", None) or creds.scopes or [])
    missing = [sc for sc in SCOPES if sc not in granted]
    if missing:
        print("Not connected: Google did not grant " + ", ".join(sc.rsplit("/", 1)[-1] for sc in missing) + ". "
              "On the Google consent screen, tick ALL the YouTube boxes. If the boxes are not shown, add these "
              "scopes under Google Cloud > OAuth consent screen > Data access, then run this again.",
              file=sys.stderr)
        return 1
    if not creds.refresh_token:
        print("Not connected: Google returned no refresh token. Remove the app's access at "
              "myaccount.google.com/permissions and run this again.", file=sys.stderr)
        return 1

    secret_id = f"/ads-mcp/{args.business_key}/youtube/config"
    secrets.put_secret(secret_id, build_config(client_id, client_secret, creds.refresh_token, args.channel_id),
                       get_settings())
    print(f"Connected. Saved to {secret_id}. Restart the youtube MCP server to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
