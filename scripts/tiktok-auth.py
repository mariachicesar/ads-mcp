"""Connect a business's TikTok account once, for scheduled inbox drafts.

    python scripts/tiktok-auth.py [--business-key el-cuis] [--redirect-uri https://mariachielcuis.com/]

Asks for the TikTok app's Client key and Client secret (or reads TIKTOK_CLIENT_KEY /
TIKTOK_CLIENT_SECRET), prints the authorize URL, and asks you to paste the address
TikTok sends you back to (or just its `code`). The code is exchanged for a refresh
token and saved to /ads-mcp/<business>/tiktok/config in Secrets Manager. Tokens are
never printed.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets as pysecrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import secrets, tiktok_client  # noqa: E402
from shared.config import get_settings  # noqa: E402

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.upload"
DEFAULT_REDIRECT = "https://mariachielcuis.com/"


def build_auth_url(client_key: str, redirect_uri: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_key": client_key, "scope": SCOPES, "response_type": "code",
        "redirect_uri": redirect_uri, "state": state,
    })


def parse_pasted(text: str, expected_state: str) -> str:
    """Return the authorization code from a pasted redirect URL (state checked) or a bare code."""
    text = text.strip()
    if "://" not in text and "?" not in text:
        if not text:
            raise ValueError("Nothing was pasted.")
        return unquote(text)
    query = {k: v[0] for k, v in parse_qs(urlparse(text).query).items()}
    if query.get("state") != expected_state:
        raise ValueError("The state in the pasted URL does not match this run; start again and use the new link.")
    if query.get("error"):
        raise ValueError(f"TikTok did not authorize: {query['error']} {query.get('error_description', '')}".rstrip())
    if not query.get("code"):
        raise ValueError("The pasted URL has no code parameter.")
    return query["code"]


def exchange_and_save(*, business_key: str, client_key: str, client_secret: str, code: str, redirect_uri: str) -> str:
    body = tiktok_client.exchange_code(client_key=client_key, client_secret=client_secret,
                                       code=code, redirect_uri=redirect_uri)
    granted = {s.strip() for s in str(body.get("scope", "")).split(",")}
    if "video.upload" not in granted:
        raise ValueError("TikTok did not grant the video.upload scope; enable it on the app and approve it when asked.")
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(body.get("refresh_expires_in") or 0))
                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
    secret_id = f"/ads-mcp/{business_key}/tiktok/config"
    secrets.put_secret(secret_id, {
        "client_key": client_key, "client_secret": client_secret, "refresh_token": body["refresh_token"],
        "open_id": body.get("open_id"), "refresh_expires_at": expires_at,
    }, get_settings())
    return secret_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--business-key", default="el-cuis")
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT)
    args = parser.parse_args()

    client_key = os.getenv("TIKTOK_CLIENT_KEY") or input("TikTok Client key: ").strip()
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET") or getpass.getpass("TikTok Client secret (hidden): ").strip()
    state = pysecrets.token_urlsafe(16)
    print("\n1. Open this link while logged in to the business TikTok account and tap Authorize:\n")
    print(build_auth_url(client_key, args.redirect_uri, state))
    print(f"\n2. TikTok sends you to {args.redirect_uri}?code=... — copy that whole address.\n")
    try:
        code = parse_pasted(input("Paste the address here: "), state)
        secret_id = exchange_and_save(business_key=args.business_key, client_key=client_key,
                                      client_secret=client_secret, code=code, redirect_uri=args.redirect_uri)
    except (ValueError, tiktok_client.AdsMcpError) as exc:
        print(f"\nNot connected: {exc}", file=sys.stderr)
        return 1
    print(f"\nConnected. Saved to {secret_id}. Scheduled TikToks for {args.business_key} can now be sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
