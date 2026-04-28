from shared.google_ads_client import build_google_ads_client, list_accessible_customers
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response
from shared.runtime_config import load_google_ads_config, load_google_ads_sdk_config


def _escape_gaql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


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


def _resolve_working_client(config: dict, tool: str) -> tuple:
    """Return (client, customer_id) using multi-strategy fallback.

    Tries in order:
      1. Customer account via manager login (standard MCC)
      2. Customer account direct (no manager)
      3. Manager account itself

    Raises AdsMcpError if all strategies fail.
    """
    configured_customer_id = config.get("customer_account_id", "").replace("-", "").strip()
    manager_id = (config.get("manager_account_id") or "").replace("-", "").strip()

    strategies = []
    if configured_customer_id:
        strategies.append((build_google_ads_client(config, tool=tool), configured_customer_id, "via-manager"))
        strategies.append((_build_google_ads_client_no_manager(config), configured_customer_id, "direct"))
    if manager_id and manager_id != configured_customer_id:
        strategies.append((build_google_ads_client(config, tool=tool), manager_id, "manager-direct"))

    last_exc = None
    for client, cid, label in strategies:
        try:
            svc = client.get_service("GoogleAdsService")
            list(svc.search(customer_id=cid, query="SELECT campaign.id FROM campaign LIMIT 1"))
            return client, cid
        except Exception as exc:
            last_exc = exc
            continue

    raise AdsMcpError(
        status_code=502,
        error_code="UPSTREAM_ERROR",
        message=(
            "Google Ads: cannot access any customer account. "
            "Ensure the account is linked to the manager in Google Ads, "
            "or that the OAuth user has direct access to the account."
        ),
        tool=tool,
        retryable=False,
        details={
            "triedStrategies": [s[2] for s in strategies],
            "reason": str(last_exc),
        },
    )


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

    working_client, customer_id = _resolve_working_client(config, "get_campaign_performance")
    google_ads_service = working_client.get_service("GoogleAdsService")

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


def list_campaigns(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="list_campaigns",
    )
    client, customer_id = _resolve_working_client(config, "list_campaigns")
    google_ads_service = client.get_service("GoogleAdsService")

    query = """
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          campaign.advertising_channel_type,
          campaign.bidding_strategy_type,
          campaign.start_date,
          campaign.end_date,
          campaign_budget.amount_micros
        FROM campaign
        WHERE campaign.status != 'REMOVED'
        ORDER BY campaign.name ASC
        LIMIT 100
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            rows.append({
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "status": row.campaign.status.name,
                "channel_type": row.campaign.advertising_channel_type.name,
                "bidding_strategy": row.campaign.bidding_strategy_type.name,
                "start_date": row.campaign.start_date,
                "end_date": row.campaign.end_date or None,
                "daily_budget": row.campaign_budget.amount_micros / 1_000_000,
                "daily_budget_micros": int(row.campaign_budget.amount_micros),
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads campaign list query failed.",
            tool="list_campaigns",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    enabled = [r for r in rows if r["status"] == "ENABLED"]
    paused = [r for r in rows if r["status"] == "PAUSED"]
    return build_success_response(
        service=SERVICE_NAME,
        tool="list_campaigns",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"{len(rows)} campaigns ({len(enabled)} enabled, {len(paused)} paused).",
        data={"campaigns": rows, "customerId": customer_id},
        freshness={"state": "live"},
    )


def get_ad_group_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_ad_group_performance",
    )
    client, customer_id = _resolve_working_client(config, "get_ad_group_performance")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    campaign_name = payload.get("campaignName")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          ad_group.status,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM ad_group
        WHERE segments.date DURING {date_range}
          AND ad_group.status != 'REMOVED'
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " ORDER BY metrics.impressions DESC LIMIT 100"

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
            ctr = clicks / impressions if impressions else 0.0
            avg_cpc = cost_micros / clicks / 1_000_000 if clicks else 0.0

            rows.append({
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "ad_group_id": str(row.ad_group.id),
                "ad_group_name": row.ad_group.name,
                "status": row.ad_group.status.name,
                "impressions": impressions,
                "clicks": clicks,
                "cost_micros": cost_micros,
                "cost": round(cost_micros / 1_000_000, 2),
                "conversions": conversions,
                "ctr": round(ctr, 4),
                "avg_cpc": round(avg_cpc, 2),
            })
            total_impressions += impressions
            total_clicks += clicks
            total_cost_micros += cost_micros
            total_conversions += conversions

    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads ad group performance query failed.",
            tool="get_ad_group_performance",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    summary_text = (
        f"{len(rows)} ad groups: {total_impressions:,} impressions, "
        f"{total_clicks:,} clicks, ${total_cost_micros / 1_000_000:.2f} spend ({date_range})."
        if rows else f"No ad group data for {date_range}."
    )
    return build_success_response(
        service=SERVICE_NAME,
        tool="get_ad_group_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=summary_text,
        data={
            "dateRange": date_range,
            "campaignFilter": campaign_name,
            "customerId": customer_id,
            "rows": rows,
            "summary": {
                "total_impressions": total_impressions,
                "total_clicks": total_clicks,
                "total_cost_micros": total_cost_micros,
                "total_conversions": total_conversions,
            },
        },
        freshness={"state": "live"},
    )


def get_keyword_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_keyword_performance",
    )
    client, customer_id = _resolve_working_client(config, "get_keyword_performance")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    campaign_name = payload.get("campaignName")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          ad_group_criterion.criterion_id,
          ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type,
          ad_group_criterion.status,
          ad_group_criterion.quality_info.quality_score,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.average_cpc
        FROM keyword_view
        WHERE segments.date DURING {date_range}
          AND ad_group_criterion.status != 'REMOVED'
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " ORDER BY metrics.impressions DESC LIMIT 200"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        total_impressions = 0
        total_clicks = 0
        total_cost_micros = 0

        for row in response:
            impressions = int(row.metrics.impressions)
            clicks = int(row.metrics.clicks)
            cost_micros = int(row.metrics.cost_micros)
            ctr = clicks / impressions if impressions else 0.0

            rows.append({
                "campaign_name": row.campaign.name,
                "ad_group_name": row.ad_group.name,
                "keyword": row.ad_group_criterion.keyword.text,
                "match_type": row.ad_group_criterion.keyword.match_type.name,
                "status": row.ad_group_criterion.status.name,
                "quality_score": row.ad_group_criterion.quality_info.quality_score or None,
                "impressions": impressions,
                "clicks": clicks,
                "cost_micros": cost_micros,
                "cost": round(cost_micros / 1_000_000, 2),
                "conversions": float(row.metrics.conversions),
                "ctr": round(ctr, 4),
                "avg_cpc": round(row.metrics.average_cpc / 1_000_000, 2),
            })
            total_impressions += impressions
            total_clicks += clicks
            total_cost_micros += cost_micros

    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads keyword performance query failed.",
            tool="get_keyword_performance",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    summary_text = (
        f"{len(rows)} keywords: {total_impressions:,} impressions, "
        f"{total_clicks:,} clicks, ${total_cost_micros / 1_000_000:.2f} spend ({date_range})."
        if rows else f"No keyword data for {date_range}."
    )
    return build_success_response(
        service=SERVICE_NAME,
        tool="get_keyword_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=summary_text,
        data={
            "dateRange": date_range,
            "campaignFilter": campaign_name,
            "customerId": customer_id,
            "rows": rows,
            "summary": {
                "total_impressions": total_impressions,
                "total_clicks": total_clicks,
                "total_cost_micros": total_cost_micros,
            },
        },
        freshness={"state": "live"},
    )


def get_search_terms_report(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_search_terms_report",
    )
    client, customer_id = _resolve_working_client(config, "get_search_terms_report")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    campaign_name = payload.get("campaignName")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          search_term_view.search_term,
          search_term_view.status,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM search_term_view
        WHERE segments.date DURING {date_range}
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " ORDER BY metrics.impressions DESC LIMIT 200"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        total_impressions = 0
        total_clicks = 0
        total_cost_micros = 0

        for row in response:
            impressions = int(row.metrics.impressions)
            clicks = int(row.metrics.clicks)
            cost_micros = int(row.metrics.cost_micros)
            ctr = clicks / impressions if impressions else 0.0

            rows.append({
                "search_term": row.search_term_view.search_term,
                "status": row.search_term_view.status.name,
                "campaign_name": row.campaign.name,
                "ad_group_name": row.ad_group.name,
                "impressions": impressions,
                "clicks": clicks,
                "cost_micros": cost_micros,
                "cost": round(cost_micros / 1_000_000, 2),
                "conversions": float(row.metrics.conversions),
                "ctr": round(ctr, 4),
            })
            total_impressions += impressions
            total_clicks += clicks
            total_cost_micros += cost_micros

    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads search terms report query failed.",
            tool="get_search_terms_report",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    summary_text = (
        f"{len(rows)} search terms: {total_impressions:,} impressions, "
        f"{total_clicks:,} clicks, ${total_cost_micros / 1_000_000:.2f} spend ({date_range})."
        if rows else f"No search term data for {date_range}."
    )
    return build_success_response(
        service=SERVICE_NAME,
        tool="get_search_terms_report",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=summary_text,
        data={
            "dateRange": date_range,
            "campaignFilter": campaign_name,
            "customerId": customer_id,
            "rows": rows,
            "summary": {
                "total_impressions": total_impressions,
                "total_clicks": total_clicks,
                "total_cost_micros": total_cost_micros,
            },
        },
        freshness={"state": "live"},
    )


def get_impression_share(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_impression_share",
    )
    client, customer_id = _resolve_working_client(config, "get_impression_share")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          metrics.search_impression_share,
          metrics.search_budget_lost_impression_share,
          metrics.search_rank_lost_impression_share,
          metrics.search_absolute_top_impression_share,
          metrics.search_top_impression_share,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros
        FROM campaign
        WHERE segments.date DURING {date_range}
          AND campaign.status != 'REMOVED'
        ORDER BY metrics.impressions DESC
        LIMIT 50
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            def _pct(v):
                try:
                    f = float(v)
                    return round(f * 100, 1) if f >= 0 else None
                except Exception:
                    return None

            rows.append({
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "status": row.campaign.status.name,
                "search_impression_share": _pct(row.metrics.search_impression_share),
                "lost_to_budget_pct": _pct(row.metrics.search_budget_lost_impression_share),
                "lost_to_rank_pct": _pct(row.metrics.search_rank_lost_impression_share),
                "abs_top_impression_share": _pct(row.metrics.search_absolute_top_impression_share),
                "top_impression_share": _pct(row.metrics.search_top_impression_share),
                "impressions": int(row.metrics.impressions),
                "clicks": int(row.metrics.clicks),
                "cost": round(row.metrics.cost_micros / 1_000_000, 2),
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads impression share query failed.",
            tool="get_impression_share",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_impression_share",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Impression share data for {len(rows)} campaigns ({date_range}).",
        data={"dateRange": date_range, "customerId": customer_id, "rows": rows},
        freshness={"state": "live"},
    )


def get_ad_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_ad_performance",
    )
    client, customer_id = _resolve_working_client(config, "get_ad_performance")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    campaign_name = payload.get("campaignName")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          ad_group_ad.ad.id,
          ad_group_ad.ad.type_,
          ad_group_ad.ad.name,
          ad_group_ad.ad.responsive_search_ad.headlines,
          ad_group_ad.ad.responsive_search_ad.descriptions,
          ad_group_ad.ad.final_urls,
          ad_group_ad.status,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.ctr,
          metrics.average_cpc
        FROM ad_group_ad
        WHERE segments.date DURING {date_range}
          AND ad_group_ad.status != 'REMOVED'
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " ORDER BY metrics.impressions DESC LIMIT 100"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            headlines = []
            descriptions = []
            try:
                for h in row.ad_group_ad.ad.responsive_search_ad.headlines:
                    headlines.append(h.text)
                for d in row.ad_group_ad.ad.responsive_search_ad.descriptions:
                    descriptions.append(d.text)
            except Exception:
                pass

            rows.append({
                "campaign_name": row.campaign.name,
                "ad_group_name": row.ad_group.name,
                "ad_id": str(row.ad_group_ad.ad.id),
                "ad_name": row.ad_group_ad.ad.name or None,
                "ad_type": row.ad_group_ad.ad.type_.name,
                "status": row.ad_group_ad.status.name,
                "headlines": headlines,
                "descriptions": descriptions,
                "final_urls": list(row.ad_group_ad.ad.final_urls),
                "impressions": int(row.metrics.impressions),
                "clicks": int(row.metrics.clicks),
                "cost": round(row.metrics.cost_micros / 1_000_000, 2),
                "conversions": float(row.metrics.conversions),
                "ctr": round(float(row.metrics.ctr), 4),
                "avg_cpc": round(row.metrics.average_cpc / 1_000_000, 2),
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads ad performance query failed.",
            tool="get_ad_performance",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_ad_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"{len(rows)} ads ({date_range}).",
        data={
            "dateRange": date_range,
            "campaignFilter": campaign_name,
            "customerId": customer_id,
            "rows": rows,
        },
        freshness={"state": "live"},
    )


def get_device_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_device_performance",
    )
    client, customer_id = _resolve_working_client(config, "get_device_performance")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    campaign_name = payload.get("campaignName")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          segments.device,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.ctr,
          metrics.average_cpc
        FROM campaign
        WHERE segments.date DURING {date_range}
          AND campaign.status != 'REMOVED'
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " ORDER BY metrics.impressions DESC"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            rows.append({
                "campaign_name": row.campaign.name,
                "device": row.segments.device.name,
                "impressions": int(row.metrics.impressions),
                "clicks": int(row.metrics.clicks),
                "cost": round(row.metrics.cost_micros / 1_000_000, 2),
                "conversions": float(row.metrics.conversions),
                "ctr": round(float(row.metrics.ctr), 4),
                "avg_cpc": round(row.metrics.average_cpc / 1_000_000, 2),
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads device performance query failed.",
            tool="get_device_performance",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_device_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Device breakdown across {len(set(r['campaign_name'] for r in rows))} campaigns ({date_range}).",
        data={
            "dateRange": date_range,
            "campaignFilter": campaign_name,
            "customerId": customer_id,
            "rows": rows,
        },
        freshness={"state": "live"},
    )


def get_geo_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_geo_performance",
    )
    client, customer_id = _resolve_working_client(config, "get_geo_performance")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    campaign_name = payload.get("campaignName")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          geographic_view.country_criterion_id,
          geographic_view.location_type,
          segments.geo_target_city,
          segments.geo_target_region,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM geographic_view
        WHERE segments.date DURING {date_range}
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " ORDER BY metrics.impressions DESC LIMIT 200"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            impressions = int(row.metrics.impressions)
            clicks = int(row.metrics.clicks)
            rows.append({
                "campaign_name": row.campaign.name,
                "location_type": row.geographic_view.location_type.name,
                "geo_target_city": row.segments.geo_target_city or None,
                "geo_target_region": row.segments.geo_target_region or None,
                "impressions": impressions,
                "clicks": clicks,
                "cost": round(row.metrics.cost_micros / 1_000_000, 2),
                "conversions": float(row.metrics.conversions),
                "ctr": round(clicks / impressions, 4) if impressions else 0.0,
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads geo performance query failed.",
            tool="get_geo_performance",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_geo_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Geo performance: {len(rows)} locations ({date_range}).",
        data={
            "dateRange": date_range,
            "campaignFilter": campaign_name,
            "customerId": customer_id,
            "rows": rows,
        },
        freshness={"state": "live"},
    )


def get_schedule_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_schedule_performance",
    )
    client, customer_id = _resolve_working_client(config, "get_schedule_performance")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    campaign_name = payload.get("campaignName")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          segments.hour,
          segments.day_of_week,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM campaign
        WHERE segments.date DURING {date_range}
          AND campaign.status != 'REMOVED'
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " ORDER BY metrics.impressions DESC"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            impressions = int(row.metrics.impressions)
            clicks = int(row.metrics.clicks)
            rows.append({
                "campaign_name": row.campaign.name,
                "day_of_week": row.segments.day_of_week.name,
                "hour": int(row.segments.hour),
                "impressions": impressions,
                "clicks": clicks,
                "cost": round(row.metrics.cost_micros / 1_000_000, 2),
                "conversions": float(row.metrics.conversions),
                "ctr": round(clicks / impressions, 4) if impressions else 0.0,
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads schedule performance query failed.",
            tool="get_schedule_performance",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_schedule_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Schedule performance by hour/day ({date_range}).",
        data={
            "dateRange": date_range,
            "campaignFilter": campaign_name,
            "customerId": customer_id,
            "rows": rows,
        },
        freshness={"state": "live"},
    )


def get_audience_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_audience_performance",
    )
    client, customer_id = _resolve_working_client(config, "get_audience_performance")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_30_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_30_DAYS"

    campaign_name = payload.get("campaignName")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          user_list.name,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM ad_group_audience_view
        WHERE segments.date DURING {date_range}
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " ORDER BY metrics.impressions DESC LIMIT 100"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            impressions = int(row.metrics.impressions)
            clicks = int(row.metrics.clicks)
            rows.append({
                "campaign_name": row.campaign.name,
                "ad_group_name": row.ad_group.name,
                "audience_name": row.user_list.name or "Unknown",
                "impressions": impressions,
                "clicks": clicks,
                "cost": round(row.metrics.cost_micros / 1_000_000, 2),
                "conversions": float(row.metrics.conversions),
                "ctr": round(clicks / impressions, 4) if impressions else 0.0,
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads audience performance query failed.",
            tool="get_audience_performance",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_audience_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Audience performance: {len(rows)} segments ({date_range}).",
        data={
            "dateRange": date_range,
            "campaignFilter": campaign_name,
            "customerId": customer_id,
            "rows": rows,
        },
        freshness={"state": "live"},
    )


def get_conversion_actions(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_conversion_actions",
    )
    client, customer_id = _resolve_working_client(config, "get_conversion_actions")
    google_ads_service = client.get_service("GoogleAdsService")

    query = """
        SELECT
          conversion_action.id,
          conversion_action.name,
          conversion_action.status,
          conversion_action.type_,
          conversion_action.category,
          conversion_action.counting_type,
          conversion_action.include_in_conversions_metric,
          conversion_action.value_settings.default_value
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        ORDER BY conversion_action.name ASC
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            rows.append({
                "conversion_id": str(row.conversion_action.id),
                "name": row.conversion_action.name,
                "status": row.conversion_action.status.name,
                "type": row.conversion_action.type_.name,
                "category": row.conversion_action.category.name,
                "counting_type": row.conversion_action.counting_type.name,
                "included_in_conversions": row.conversion_action.include_in_conversions_metric,
                "default_value": row.conversion_action.value_settings.default_value,
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads conversion actions query failed.",
            tool="get_conversion_actions",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_conversion_actions",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"{len(rows)} conversion actions configured.",
        data={"customerId": customer_id, "rows": rows},
        freshness={"state": "live"},
    )


def get_change_history(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_change_history",
    )
    client, customer_id = _resolve_working_client(config, "get_change_history")
    google_ads_service = client.get_service("GoogleAdsService")

    payload = request.payload or {}
    date_range = payload.get("dateRange", "LAST_14_DAYS")
    valid_ranges = {
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "THIS_MONTH", "LAST_MONTH",
    }
    if date_range not in valid_ranges:
        date_range = "LAST_14_DAYS"

    query = f"""
        SELECT
          change_event.change_date_time,
          change_event.change_resource_type,
          change_event.changed_fields,
          change_event.client_type,
          change_event.resource_change_operation,
          change_event.user_email,
          change_event.campaign,
          change_event.ad_group
        FROM change_event
        WHERE change_event.change_date_time DURING {date_range}
        ORDER BY change_event.change_date_time DESC
        LIMIT 100
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            rows.append({
                "change_datetime": row.change_event.change_date_time,
                "resource_type": row.change_event.change_resource_type.name,
                "operation": row.change_event.resource_change_operation.name,
                "changed_fields": row.change_event.changed_fields,
                "client_type": row.change_event.client_type.name,
                "user_email": row.change_event.user_email or None,
                "campaign_resource": row.change_event.campaign or None,
                "ad_group_resource": row.change_event.ad_group or None,
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads change history query failed.",
            tool="get_change_history",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_change_history",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"{len(rows)} changes in the last {date_range.lower().replace('_', ' ')}.",
        data={"dateRange": date_range, "customerId": customer_id, "rows": rows},
        freshness={"state": "live"},
    )


def get_recommendations(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="get_recommendations",
    )
    client, customer_id = _resolve_working_client(config, "get_recommendations")
    google_ads_service = client.get_service("GoogleAdsService")

    query = """
        SELECT
          recommendation.type_,
          recommendation.impact.base_metrics.impressions,
          recommendation.impact.potential_metrics.impressions,
          recommendation.impact.base_metrics.clicks,
          recommendation.impact.potential_metrics.clicks,
          recommendation.campaign,
          recommendation.ad_group,
          recommendation.resource_name
        FROM recommendation
        ORDER BY recommendation.type_ ASC
        LIMIT 50
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        rows = []
        for row in response:
            base_impressions = int(row.recommendation.impact.base_metrics.impressions)
            potential_impressions = int(row.recommendation.impact.potential_metrics.impressions)
            base_clicks = int(row.recommendation.impact.base_metrics.clicks)
            potential_clicks = int(row.recommendation.impact.potential_metrics.clicks)
            rows.append({
                "type": row.recommendation.type_.name,
                "resource_name": row.recommendation.resource_name,
                "campaign_resource": row.recommendation.campaign or None,
                "ad_group_resource": row.recommendation.ad_group or None,
                "base_impressions": base_impressions,
                "potential_impressions": potential_impressions,
                "impressions_lift": potential_impressions - base_impressions,
                "base_clicks": base_clicks,
                "potential_clicks": potential_clicks,
                "clicks_lift": potential_clicks - base_clicks,
            })
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads recommendations query failed.",
            tool="get_recommendations",
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    return build_success_response(
        service=SERVICE_NAME,
        tool="get_recommendations",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"{len(rows)} optimization recommendations available.",
        data={"customerId": customer_id, "rows": rows},
        freshness={"state": "live"},
    )
