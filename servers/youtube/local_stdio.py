"""Run the YouTube MCP server over stdio for Claude Code.

The schedule tools write the S3 media queue, so ADS_MCP_MEDIA_BUCKET must be
set (from the environment or the repo's .env; ADS_MCP_DOTENV points at an
alternate .env). The YouTube config secret (/ads-mcp/<business>/youtube/config)
and S3 use AWS credentials from the usual AWS chain.

    python servers/youtube/local_stdio.py
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main() -> None:
    from dotenv import dotenv_values

    dotenv_path = Path(os.getenv("ADS_MCP_DOTENV") or ROOT / ".env")
    values = dotenv_values(dotenv_path) if dotenv_path.is_file() else {}
    bucket = os.getenv("ADS_MCP_MEDIA_BUCKET") or values.get("ADS_MCP_MEDIA_BUCKET")
    if not bucket:
        sys.exit("youtube local_stdio: ADS_MCP_MEDIA_BUCKET is not set in .env or the environment")
    os.environ["ADS_MCP_MEDIA_BUCKET"] = bucket
    os.environ.setdefault("ADS_MCP_REQUIRE_SIGNED_REQUESTS", "false")

    sys.path.insert(0, str(HERE))
    runpy.run_path(str(HERE / "mcp_server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
