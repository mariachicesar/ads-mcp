"""
Get a Google OAuth refresh token for Google Ads / GA4 / Search Console.

Usage:
    CLIENT_ID=your_client_id CLIENT_SECRET=your_client_secret python scripts/get-refresh-token.py

Or set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your environment.
"""
import os
import sys
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
from google_auth_oauthlib.flow import InstalledAppFlow

client_id = os.environ.get("GOOGLE_CLIENT_ID") or os.environ.get("CLIENT_ID")
client_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or os.environ.get("CLIENT_SECRET")

if not client_id or not client_secret:
    print("ERROR: Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables.")
    sys.exit(1)

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    },
    scopes=[
        "https://www.googleapis.com/auth/adwords",
        "https://www.googleapis.com/auth/analytics.readonly",
        "https://www.googleapis.com/auth/webmasters.readonly",
        "openid",
        "email",
    ],
)
creds = flow.run_local_server(port=8085)
print("Refresh token:", creds.refresh_token)

# Verify which account this token belongs to
import urllib.request, json as _json
token_resp = urllib.request.urlopen(
    f"https://www.googleapis.com/oauth2/v3/tokeninfo?access_token={creds.token}"
)
token_info = _json.loads(token_resp.read())
print("Authorized account email:", token_info.get("email", "unknown"))
print("Sub (user ID):", token_info.get("sub", "unknown"))