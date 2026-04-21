"""Content Agent MCP server — FastMCP protocol layer.

Wraps the Content Agent and exposes it via the MCP protocol for Claude Desktop.

Run via stdio (for Claude Desktop):
    python servers/content-agent/mcp_server.py

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

BRANDS_DIR = Path(__file__).resolve().parent / "brands"

mcp = FastMCP(
    name="content-agent",
    instructions=(
        "Tools for generating ad copy, landing page content, and social posts "
        "for RnR Electrician and GQ Custom Painting. "
        "Always use the correct business key so brand voice and service area are accurate. "
        "Content generation requires Claude API integration — currently returns drafts as placeholders."
    ),
)


@mcp.tool()
def content_write_google_ad(
    business_key: Annotated[str, Field(description="Business key: 'rnr-electrician' or 'gq-painting'")],
    keyword: Annotated[str, Field(description="Target keyword for the ad, e.g. 'electrician near me'")],
    city: Annotated[str, Field(description="Target city for geo-specific copy, e.g. 'Pasadena'")] = "",
    topic: Annotated[str, Field(description="Optional topic or angle for the ad, e.g. 'emergency service'")] = "",
) -> dict:
    """Generate Google Ads headlines and descriptions for a given keyword and city.

    Loads brand voice and service context from the business brand file.
    Note: Claude API content generation not yet wired up. Returns placeholder draft.
    """
    brand_file = BRANDS_DIR / f"{business_key}.md"
    brand_context = brand_file.read_text(encoding="utf-8") if brand_file.exists() else ""

    from shared.responses import build_success_response
    return build_success_response(
        service="content",
        tool="write_google_ad",
        mode="draft",
        business_key=business_key,
        request_id=None,
        summary="Placeholder content generation response.",
        data={
            "contentType": "google_ad",
            "keyword": keyword,
            "city": city,
            "topic": topic,
            "brandContextLoaded": bool(brand_context),
            "draft": {
                "headlines": [],
                "descriptions": [],
                "note": "Claude API content generation not yet wired up.",
            },
        },
        requires_confirmation=False,
        executed=False,
    )


if __name__ == "__main__":
    mcp.run()
