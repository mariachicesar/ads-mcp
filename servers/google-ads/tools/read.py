from shared.google_ads_client import build_google_ads_client, get_google_ads_customer_id, list_accessible_customers
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response
from shared.runtime_config import load_google_ads_config, load_google_ads_sdk_config


SERVICE_NAME = "google-ads"


def _build_account_context(config: dict) -> dict:
    return {
        "customerAccountId": config.get("customer_account_id"),
        "managerAccountId": config.get("manager_account_id"),
        "hasDeveloperToken": bool(config.get("developer_token")),
        "hasRefreshToken": bool(config.get("refresh_token")),
    }


def list_accounts(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="list_accounts",
    )
    accounts = list_accessible_customers(config, tool="list_accounts")
    return build_success_response(
        service=SERVICE_NAME,
        tool="list_accounts",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary="Retrieved accessible Google Ads accounts.",
        data={
            "accounts": accounts,
            "configuredCustomerAccountId": config.get("customer_account_id"),
            "configuredManagerAccountId": config.get("manager_account_id"),
            "configSource": "runtime-sdk",
        },
        freshness={"state": "live"},
    )


def _get_client_customer_ids(client, manager_id: str, tool: str) -> list[str]:
    """Return non-manager customer IDs linked under the manager account."""
    google_ads_service = client.get_service("GoogleAdsService")
    query = """
        SELECT
          customer_client.client_customer,
          customer_client.manager,
          customer_client.level
        FROM customer_client
        WHERE customer_client.manager = FALSE
          AND customer_client.level = 1
    """
    try:
        response = google_ads_service.search(customer_id=manager_id, query=query)
        return [
            row.customer_client.client_customer.split("/")[-1]
            for row in response
        ]
    except Exception:
        return []


def _build_google_ads_client_no_manager(config: dict):
    """Build a Google Ads client WITHOUT login_customer_id for direct account access."""
    from shared.google_ads_client import build_google_ads_client as _build
    import copy
    cfg = copy.copy(config)
    cfg.pop("manager_account_id", None)
    cfg.pop("login_customer_id", None)
    return _build(cfg, tool="get_campaign_performance")


def get_campaign_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_config(
        business_key=request.businessKey,
        tool="get_campaign_performance",
    )

    date_range = (request.payload or {}).get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    configured_customer_id = config.get("customer_account_id", "").replace("-", "").strip()
    manager_id = (config.get("manager_account_id") or "").replace("-", "").strip()

    # Strategy: try three client/customer combinations in order:
    #   1. Configured customer via manager (standard MCC setup)
    #   2. Configured customer direct (no manager login_customer_id)
    #   3. Manager account itself (edge case: ads run from manager)
    strategies = []
    if configured_customer_id:
        client_with_manager = build_google_ads_client(config, tool="get_campaign_performance")
        strategies.append((client_with_manager, configured_customer_id, "via-manager"))
        client_direct = _build_google_ads_client_no_manager(config)
        strategies.append((client_direct, configured_customer_id, "direct"))
    if manager_id and manager_id != configured_customer_id:
        client_with_manager = build_google_ads_client(config, tool="get_campaign_performance")
        strategies.append((client_with_manager, manager_id, "manager-direct"))

    test_query = f"SELECT campaign.id FROM campaign WHERE segments.date DURING {date_range} LIMIT 1"
    working_client = None
    working_customer_id = None
    last_exc = None

    for client, cid, label in strategies:
        try:
            svc = client.get_service("GoogleAdsService")
            list(svc.search(customer_id=cid, query=test_query))
            working_client = client
            working_customer_id = cid
            break
        except Exception as exc:
            last_exc = exc
            continue

    if working_client is None:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message=(
                "Google Ads: cannot access any customer account. "
                "Ensure account 1057140994 is linked to manager 4746289774 in Google Ads, "
                "or that rctechconsulting1@gmail.com has direct access to that account."
            ),
            tool="get_campaign_performance",
            retryable=False,
            details={
                "triedStrategies": [s[2] for s in strategies],
                "reason": str(last_exc),
            },
        )

    google_ads_service = working_client.get_service("GoogleAdsService")
    customer_id = working_customer_id

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM campaign
        WHERE segments.date DURING {date_range}
          AND campaign.status != 'REMOVED'
        ORDER BY metrics.impressions DESC
        LIMIT 50
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        total_impressions = 0
        total_clicks = 0
        total_cost_micros = 0
        total_conversions = 0.0

        for row in response:
            impressions = int(row.metrics.impressions)
            clicks = int(row.metrics.clicks)
            cost_micros = int(row.metrics.cost_micros)
            conversions = float(row.metrics.conversions)

            rows.append({
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "status": row.campaign.status.name,
                "impressions": impressions,
                "clicks": clicks,
                "cost_micros": cost_micros,
                "conversions": conversions,
            })
            total_impressions += impressions
            total_clicks += clicks
            total_cost_micros += cost_micros
            total_conversions += conversions

    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads campaign performance query failed.",
            tool="get_campaign_performance",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    summary_text = (
        f"{len(rows)} campaigns: {total_impressions:,} impressions, "
        f"{total_clicks:,} clicks, ${total_cost_micros / 1_000_000:.2f} spend ({date_range})."
        if rows else f"No campaign data for {date_range}."
    )

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_campaign_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=summary_text,
        data={
            "dateRange": date_range,
            "customerId": customer_id,
            "rows": rows,
            "summary": {
                "total_impressions": total_impressions,
                "total_clicks": total_clicks,
                "total_cost_micros": total_cost_micros,
                "total_conversions": total_conversions,
            },
            "accountContext": _build_account_context(config),
        },
        freshness={"state": "live"},
    )
