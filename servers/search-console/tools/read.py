"""Search Console read tools — real Google Search Console API implementation."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response
from shared.runtime_config import load_platform_runtime_config


def _build_client(config: dict[str, Any]):
    """Build an authenticated Search Console service client."""
    creds = Credentials(
        token=None,
        refresh_token=config["refresh_token"],
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    return service, config["site_url"]


def _date_range_to_dates(date_range: str) -> tuple[str, str]:
    today = date.today()
    days = {
        "LAST_7_DAYS": 7,
        "LAST_28_DAYS": 28,
        "LAST_30_DAYS": 30,
        "LAST_90_DAYS": 90,
    }.get(date_range.upper(), 30)
    # Search Console has ~3 day lag; end at 3 days ago
    end = today - timedelta(days=3)
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def get_search_performance(req: ToolRequest, request_id: str | None) -> dict:
    """Return top queries/pages from Search Console with impressions, clicks, CTR, position."""
    config = load_platform_runtime_config(
        platform="search-console",
        business_key=req.businessKey,
        required_keys=("site_url", "client_id", "client_secret", "refresh_token"),
        tool="get_search_performance",
    )

    payload = req.payload or {}
    date_range_str = payload.get("dateRange", "LAST_30_DAYS")
    dimension = payload.get("dimension", "query")
    limit = int(payload.get("limit", 25))

    if dimension not in ("query", "page", "country", "device"):
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"Invalid dimension '{dimension}'. Must be query, page, country, or device.",
            tool="get_search_performance",
        )

    start_date, end_date = _date_range_to_dates(date_range_str)

    try:
        service, site_url = _build_client(config)
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": [dimension],
            "rowLimit": limit,
            "startRow": 0,
        }
        response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Search Console API request failed.",
            tool="get_search_performance",
            details={"reason": str(exc)},
        ) from exc

    rows = []
    for row in response.get("rows", []):
        rows.append({
            dimension: row["keys"][0],
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "ctrPct": round(row.get("ctr", 0) * 100, 2),
            "position": round(row.get("position", 0), 1),
        })

    totals = {
        "clicks": sum(r["clicks"] for r in rows),
        "impressions": sum(r["impressions"] for r in rows),
    }

    return build_success_response(
        service="search-console",
        tool="get_search_performance",
        mode="read",
        business_key=req.businessKey,
        request_id=request_id,
        summary=(
            f"Search Console: {totals['clicks']} clicks, {totals['impressions']} impressions "
            f"({date_range_str}, by {dimension})."
        ),
        data={
            "dateRange": date_range_str,
            "dimension": dimension,
            "siteUrl": config["site_url"],
            "dateStart": start_date,
            "dateEnd": end_date,
            "totals": totals,
            "rows": rows,
        },
        freshness={"state": "live"},
    )
