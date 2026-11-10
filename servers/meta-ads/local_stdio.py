"""Run the Meta Ads MCP server over stdio for Claude Code, using credentials from .env.

Builds the per-business Meta config from META_* values in the repo's .env (or the
process environment) and hands it to the server through
ADS_MCP_META_ADS_CONFIGS_JSON in this process only. Nothing is written to disk
or to Secrets Manager, and no secrets need to live in ~/.claude.json.

    python servers/meta-ads/local_stdio.py

Reads: META_TOKEN, META_AD_ACCOUNT_ID, META_PAGE_ID, META_APP_ID, META_APP_SECRET,
META_API_VERSION (default v23.0). Optional: META_BUSINESS_KEY (default el-cuis),
META_DISPLAY_NAME, ADS_MCP_DOTENV (path to an alternate .env).
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _load_env() -> dict[str, str]:
    from dotenv import dotenv_values

    dotenv_path = Path(os.getenv("ADS_MCP_DOTENV") or ROOT / ".env")
    values = {k: v for k, v in dotenv_values(dotenv_path).items() if v} if dotenv_path.is_file() else {}
    # Real environment variables win over .env.
    values.update({k: v for k, v in os.environ.items() if k.startswith("META_") and v})
    return values


def main() -> None:
    env = _load_env()
    missing = [k for k in ("META_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID", "META_APP_ID", "META_APP_SECRET") if not env.get(k)]
    if missing:
        sys.exit(f"meta-ads local_stdio: missing {', '.join(missing)} in .env or environment")

    business_key = env.get("META_BUSINESS_KEY", "el-cuis")
    config = {
        "access_token": env["META_TOKEN"],
        "ad_account_id": env["META_AD_ACCOUNT_ID"],
        "page_id": env["META_PAGE_ID"],
        "app_id": env["META_APP_ID"],
        "app_secret": env["META_APP_SECRET"],
        "api_version": env.get("META_API_VERSION", "v23.0"),
    }
    os.environ["ADS_MCP_META_ADS_CONFIGS_JSON"] = json.dumps({business_key: config})
    # The stored manifest may still have meta-ads disabled; enable it for this process only.
    os.environ["ADS_MCP_CLIENT_MANIFESTS_JSON"] = json.dumps({
        business_key: {
            "businessKey": business_key,
            "displayName": env.get("META_DISPLAY_NAME", business_key),
            "platforms": {"meta-ads": {"enabled": True}},
        }
    })
    os.environ.setdefault("ADS_MCP_REQUIRE_SIGNED_REQUESTS", "false")

    sys.path.insert(0, str(HERE))
    runpy.run_path(str(HERE / "mcp_server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
