"""Search Console MCP server — FastMCP protocol layer.

Wraps the Search Console service and exposes it via the MCP protocol for Claude Desktop.

Run via stdio (for Claude Desktop):
    python servers/search-console/mcp_server.py

Required env vars:
    ADS_MCP_REQUIRE_SIGNED_REQUESTS  — set to "false" for local dev
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from shared.models import ToolRequest
from tools.read import get_search_performance

mcp = FastMCP(
    name="search-console",
    instructions=(
        "Tools for viewing Google Search Console organic search data. "
        "Requires site_url in the business config (local-dev-config.json or Secrets Manager). "
        "Always use business_key 'rnr-electrician' or 'gq-painting'."
    ),
)


@mcp.tool()
def search_console_get_search_performance(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    date_range: Annotated[str, Field(description="Date range: LAST_7_DAYS, LAST_28_DAYS, LAST_30_DAYS, LAST_90_DAYS")] = "LAST_30_DAYS",
    dimension: Annotated[str, Field(description="Group results by: 'query', 'page', 'country', or 'device'")] = "query",
    limit: Annotated[int, Field(description="Max rows to return", ge=1, le=100)] = 25,
) -> dict:
    """Get organic search performance from Search Console: clicks, impressions, CTR, and average position."""
    req = ToolRequest(businessKey=business_key, payload={"dateRange": date_range, "dimension": dimension, "limit": limit})
    return get_search_performance(req, request_id=None)


if __name__ == "__main__":
    mcp.run()
