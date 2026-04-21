"""GA4 Analytics read tools — real Google Analytics Data API implementation."""

from __future__ import annotations

from typing import Any

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    OrderBy,
)
from google.oauth2.credentials import Credentials

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response
from shared.runtime_config import load_platform_runtime_config


def _build_client(config: dict[str, Any]) -> tuple[BetaAnalyticsDataClient, str]:
    """Build an authenticated GA4 client and return (client, property_id)."""
    creds = Credentials(
        token=None,
        refresh_token=config["refresh_token"],
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    client = BetaAnalyticsDataClient(credentials=creds)
    property_id = str(config["property_id"])
    return client, property_id


_DATE_RANGE_MAP = {
    "LAST_7_DAYS": "7daysAgo",
    "LAST_14_DAYS": "14daysAgo",
    "LAST_28_DAYS": "28daysAgo",
    "LAST_30_DAYS": "30daysAgo",
    "THIS_MONTH": "30daysAgo",  # approximate
    "LAST_90_DAYS": "90daysAgo",
}


def _resolve_date_range(date_range: str) -> tuple[str, str]:
    start = _DATE_RANGE_MAP.get(date_range.upper(), "30daysAgo")
    return start, "today"


def get_traffic_overview(req: ToolRequest, request_id: str | None) -> dict:
    """Return GA4 session/user/conversion data broken down by channel."""
    config = load_platform_runtime_config(
        platform="analytics",
        business_key=req.businessKey,
        required_keys=("property_id", "client_id", "client_secret", "refresh_token"),
        tool="get_traffic_overview",
    )

    date_range_str = (req.payload or {}).get("dateRange", "LAST_30_DAYS")
    start_date, end_date = _resolve_date_range(date_range_str)

    try:
        client, property_id = _build_client(config)
        response = client.run_report(
            RunReportRequest(
                property=f"properties/{property_id}",
                dimensions=[
                    Dimension(name="sessionDefaultChannelGroup"),
                ],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="totalUsers"),
                    Metric(name="bounceRate"),
                    Metric(name="conversions"),
                    Metric(name="averageSessionDuration"),
                ],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                order_bys=[
                    OrderBy(
                        metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                        desc=True,
                    )
                ],
                limit=20,
            )
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GA4 API request failed.",
            tool="get_traffic_overview",
            details={"reason": str(exc)},
        ) from exc

    rows = []
    for row in response.rows:
        channel = row.dimension_values[0].value
        sessions = int(row.metric_values[0].value)
        users = int(row.metric_values[1].value)
        bounce_rate = round(float(row.metric_values[2].value) * 100, 1)
        conversions = int(row.metric_values[3].value)
        avg_duration_s = round(float(row.metric_values[4].value), 1)
        rows.append({
            "channel": channel,
            "sessions": sessions,
            "users": users,
            "bounceRatePct": bounce_rate,
            "conversions": conversions,
            "avgSessionDurationSec": avg_duration_s,
        })

    totals = {
        "sessions": sum(r["sessions"] for r in rows),
        "users": sum(r["users"] for r in rows),
        "conversions": sum(r["conversions"] for r in rows),
    }

    return build_success_response(
        service="analytics",
        tool="get_traffic_overview",
        mode="read",
        business_key=req.businessKey,
        request_id=request_id,
        summary=f"GA4 traffic overview: {totals['sessions']} sessions, {totals['conversions']} conversions ({date_range_str}).",
        data={
            "dateRange": date_range_str,
            "propertyId": config["property_id"],
            "totals": totals,
            "byChannel": rows,
        },
        freshness={"state": "live"},
    )


def get_top_pages(req: ToolRequest, request_id: str | None) -> dict:
    """Return top landing pages by sessions."""
    config = load_platform_runtime_config(
        platform="analytics",
        business_key=req.businessKey,
        required_keys=("property_id", "client_id", "client_secret", "refresh_token"),
        tool="get_top_pages",
    )

    date_range_str = (req.payload or {}).get("dateRange", "LAST_30_DAYS")
    limit = int((req.payload or {}).get("limit", 10))
    start_date, end_date = _resolve_date_range(date_range_str)

    try:
        client, property_id = _build_client(config)
        response = client.run_report(
            RunReportRequest(
                property=f"properties/{property_id}",
                dimensions=[Dimension(name="pagePath")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="conversions"),
                    Metric(name="bounceRate"),
                ],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                order_bys=[
                    OrderBy(
                        metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                        desc=True,
                    )
                ],
                limit=limit,
            )
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="GA4 API request failed.",
            tool="get_top_pages",
            details={"reason": str(exc)},
        ) from exc

    rows = []
    for row in response.rows:
        rows.append({
            "page": row.dimension_values[0].value,
            "sessions": int(row.metric_values[0].value),
            "conversions": int(row.metric_values[1].value),
            "bounceRatePct": round(float(row.metric_values[2].value) * 100, 1),
        })

    return build_success_response(
        service="analytics",
        tool="get_top_pages",
        mode="read",
        business_key=req.businessKey,
        request_id=request_id,
        summary=f"Top {len(rows)} pages by sessions ({date_range_str}).",
        data={"dateRange": date_range_str, "pages": rows},
        freshness={"state": "live"},
    )
