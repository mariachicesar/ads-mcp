from __future__ import annotations

from importlib import import_module
from typing import Any

from shared.errors import AdsMcpError


def _normalize_customer_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("-", "").strip()


def build_google_ads_client(config: dict[str, Any], *, tool: str | None = None):
    try:
        google_ads_client_module = import_module("google.ads.googleads.client")
    except ModuleNotFoundError as exc:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="google-ads SDK is not installed in the current environment.",
            tool=tool,
        ) from exc

    client_config: dict[str, Any] = {
        "developer_token": config.get("developer_token"),
        "client_id": config.get("client_id"),
        "client_secret": config.get("client_secret"),
        "refresh_token": config.get("refresh_token"),
        "use_proto_plus": True,
    }

    login_customer_id = _normalize_customer_id(
        config.get("login_customer_id") or config.get("manager_account_id")
    )
    if login_customer_id:
        client_config["login_customer_id"] = login_customer_id

    try:
        return google_ads_client_module.GoogleAdsClient.load_from_dict(client_config)
    except Exception as exc:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Google Ads client configuration could not be initialized.",
            tool=tool,
            details={"reason": str(exc)},
        ) from exc


def list_accessible_customers(config: dict[str, Any], *, tool: str | None = None) -> list[dict[str, Any]]:
    client = build_google_ads_client(config, tool=tool)

    try:
        customer_service = client.get_service("CustomerService")
        response = customer_service.list_accessible_customers()
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads accessible customer lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    configured_customer_id = _normalize_customer_id(config.get("customer_account_id"))
    configured_manager_id = _normalize_customer_id(config.get("manager_account_id"))

    accounts: list[dict[str, Any]] = []
    for resource_name in response.resource_names:
        customer_id = resource_name.split("/")[-1]
        accounts.append(
            {
                "resourceName": resource_name,
                "customerAccountId": customer_id,
                "isConfiguredCustomer": customer_id == configured_customer_id,
                "isConfiguredManager": customer_id == configured_manager_id,
            }
        )

    return accounts


def get_google_ads_customer_id(config: dict[str, Any], *, tool: str | None = None) -> str:
    customer_id = _normalize_customer_id(config.get("customer_account_id"))
    if not customer_id:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Google Ads customer account ID is not configured.",
            tool=tool,
        )
    return customer_id


def _escape_gaql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def get_campaign_budget_snapshot(
    config: dict[str, Any],
    *,
    campaign_name: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          campaign.campaign_budget,
          campaign_budget.resource_name,
          campaign_budget.amount_micros
        FROM campaign
        WHERE campaign.name = '{_escape_gaql_string(campaign_name)}'
        LIMIT 1
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        row = next(iter(response), None)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads campaign lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if row is None:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Campaign '{campaign_name}' was not found in Google Ads.",
            tool=tool,
            details={"campaignName": campaign_name},
        )

    amount_micros = int(row.campaign_budget.amount_micros)
    return {
        "customerAccountId": customer_id,
        "campaignId": str(row.campaign.id),
        "campaignName": row.campaign.name,
        "campaignStatus": row.campaign.status.name,
        "campaignBudgetResourceName": row.campaign_budget.resource_name,
        "currentBudgetMicros": amount_micros,
        "currentDailyBudget": amount_micros / 1_000_000,
    }


def get_campaign_status_snapshot(
    config: dict[str, Any],
    *,
    campaign_name: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          campaign.id,
          campaign.resource_name,
          campaign.name,
          campaign.status
        FROM campaign
        WHERE campaign.name = '{_escape_gaql_string(campaign_name)}'
          AND campaign.status != 'REMOVED'
        LIMIT 1
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        row = next(iter(response), None)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads campaign lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if row is None:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Campaign '{campaign_name}' was not found in Google Ads.",
            tool=tool,
            details={"campaignName": campaign_name},
        )

    return {
        "customerAccountId": customer_id,
        "campaignId": str(row.campaign.id),
        "campaignName": row.campaign.name,
        "campaignResourceName": row.campaign.resource_name,
        "currentStatus": row.campaign.status.name,
    }


def mutate_campaign_status(
    config: dict[str, Any],
    *,
    campaign_resource_name: str,
    new_status: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)

    try:
        protobuf_helpers = import_module("google.api_core.protobuf_helpers")
        campaign_service = client.get_service("CampaignService")
        operation = client.get_type("CampaignOperation")
        campaign = operation.update
        campaign.resource_name = campaign_resource_name
        status_enum = client.enums.CampaignStatusEnum
        campaign.status = getattr(status_enum, new_status)
        client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, campaign._pb),
        )
        response = campaign_service.mutate_campaigns(
            customer_id=customer_id,
            operations=[operation],
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads campaign status update failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "campaignResourceName": getattr(result, "resource_name", campaign_resource_name),
        "newStatus": new_status,
    }


def get_ad_group_status_snapshot(
    config: dict[str, Any],
    *,
    ad_group_name: str,
    campaign_name: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          ad_group.id,
          ad_group.resource_name,
          ad_group.name,
          ad_group.status,
          campaign.name
        FROM ad_group
        WHERE ad_group.name = '{_escape_gaql_string(ad_group_name)}'
          AND ad_group.status != 'REMOVED'
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    query += " LIMIT 1"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        row = next(iter(response), None)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads ad group lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if row is None:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Ad group '{ad_group_name}' was not found in Google Ads.",
            tool=tool,
            details={"adGroupName": ad_group_name, "campaignName": campaign_name},
        )

    return {
        "customerAccountId": customer_id,
        "adGroupId": str(row.ad_group.id),
        "adGroupName": row.ad_group.name,
        "adGroupResourceName": row.ad_group.resource_name,
        "campaignName": row.campaign.name,
        "currentStatus": row.ad_group.status.name,
    }


def mutate_ad_group_status(
    config: dict[str, Any],
    *,
    ad_group_resource_name: str,
    new_status: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)

    try:
        protobuf_helpers = import_module("google.api_core.protobuf_helpers")
        ad_group_service = client.get_service("AdGroupService")
        operation = client.get_type("AdGroupOperation")
        ad_group = operation.update
        ad_group.resource_name = ad_group_resource_name
        status_enum = client.enums.AdGroupStatusEnum
        ad_group.status = getattr(status_enum, new_status)
        client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, ad_group._pb),
        )
        response = ad_group_service.mutate_ad_groups(
            customer_id=customer_id,
            operations=[operation],
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads ad group status update failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "adGroupResourceName": getattr(result, "resource_name", ad_group_resource_name),
        "newStatus": new_status,
    }


def update_campaign_budget_amount(
    config: dict[str, Any],
    *,
    budget_resource_name: str,
    new_budget_micros: int,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)

    try:
        protobuf_helpers = import_module("google.api_core.protobuf_helpers")
        campaign_budget_service = client.get_service("CampaignBudgetService")
        operation = client.get_type("CampaignBudgetOperation")
        budget = operation.update
        budget.resource_name = budget_resource_name
        budget.amount_micros = int(new_budget_micros)
        client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, budget._pb),
        )
        response = campaign_budget_service.mutate_campaign_budgets(
            customer_id=customer_id,
            operations=[operation],
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads campaign budget update failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "budgetResourceName": getattr(result, "resource_name", budget_resource_name),
        "newBudgetMicros": int(new_budget_micros),
        "newDailyBudget": int(new_budget_micros) / 1_000_000,
    }


def get_keyword_snapshot(
    config: dict[str, Any],
    *,
    keyword_text: str,
    campaign_name: str | None = None,
    ad_group_name: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          ad_group_criterion.criterion_id,
          ad_group_criterion.resource_name,
          ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type,
          ad_group_criterion.status,
          ad_group_criterion.cpc_bid_micros,
          ad_group.id,
          ad_group.name,
          campaign.id,
          campaign.name
        FROM ad_group_criterion
        WHERE ad_group_criterion.type = KEYWORD
          AND ad_group_criterion.keyword.text = '{_escape_gaql_string(keyword_text)}'
          AND ad_group_criterion.status != 'REMOVED'
    """
    if campaign_name:
        query += f" AND campaign.name = '{_escape_gaql_string(campaign_name)}'"
    if ad_group_name:
        query += f" AND ad_group.name = '{_escape_gaql_string(ad_group_name)}'"
    query += " LIMIT 1"

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        row = next(iter(response), None)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads keyword lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if row is None:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Keyword '{keyword_text}' was not found.",
            tool=tool,
            details={"keyword": keyword_text, "campaignName": campaign_name, "adGroupName": ad_group_name},
        )

    return {
        "customerAccountId": customer_id,
        "criterionId": str(row.ad_group_criterion.criterion_id),
        "resourceName": row.ad_group_criterion.resource_name,
        "keywordText": row.ad_group_criterion.keyword.text,
        "matchType": row.ad_group_criterion.keyword.match_type.name,
        "status": row.ad_group_criterion.status.name,
        "currentCpcBidMicros": int(row.ad_group_criterion.cpc_bid_micros),
        "currentCpcBid": int(row.ad_group_criterion.cpc_bid_micros) / 1_000_000,
        "adGroupId": str(row.ad_group.id),
        "adGroupName": row.ad_group.name,
        "campaignId": str(row.campaign.id),
        "campaignName": row.campaign.name,
    }


def mutate_keyword_bid(
    config: dict[str, Any],
    *,
    keyword_resource_name: str,
    new_cpc_bid_micros: int,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)

    try:
        protobuf_helpers = import_module("google.api_core.protobuf_helpers")
        ad_group_criterion_service = client.get_service("AdGroupCriterionService")
        operation = client.get_type("AdGroupCriterionOperation")
        criterion = operation.update
        criterion.resource_name = keyword_resource_name
        criterion.cpc_bid_micros = int(new_cpc_bid_micros)
        client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, criterion._pb),
        )
        response = ad_group_criterion_service.mutate_ad_group_criteria(
            customer_id=customer_id,
            operations=[operation],
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads keyword bid update failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "criterionResourceName": getattr(result, "resource_name", keyword_resource_name),
        "newCpcBidMicros": int(new_cpc_bid_micros),
        "newCpcBid": int(new_cpc_bid_micros) / 1_000_000,
    }


def get_ad_snapshot(
    config: dict[str, Any],
    *,
    ad_id: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          ad_group_ad.ad.id,
          ad_group_ad.ad.name,
          ad_group_ad.ad.type_,
          ad_group_ad.resource_name,
          ad_group_ad.status,
          ad_group.id,
          ad_group.name,
          campaign.id,
          campaign.name
        FROM ad_group_ad
        WHERE ad_group_ad.ad.id = {_escape_gaql_string(str(ad_id))}
          AND ad_group_ad.status != 'REMOVED'
        LIMIT 1
    """

    try:
        response = google_ads_service.search(customer_id=customer_id, query=query)
        row = next(iter(response), None)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads ad lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if row is None:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Ad ID '{ad_id}' was not found.",
            tool=tool,
            details={"adId": ad_id},
        )

    return {
        "customerAccountId": customer_id,
        "adId": str(row.ad_group_ad.ad.id),
        "adName": row.ad_group_ad.ad.name or None,
        "adType": row.ad_group_ad.ad.type_.name,
        "adResourceName": row.ad_group_ad.resource_name,
        "currentStatus": row.ad_group_ad.status.name,
        "adGroupId": str(row.ad_group.id),
        "adGroupName": row.ad_group.name,
        "campaignId": str(row.campaign.id),
        "campaignName": row.campaign.name,
    }


def mutate_ad_status(
    config: dict[str, Any],
    *,
    ad_resource_name: str,
    new_status: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)

    try:
        protobuf_helpers = import_module("google.api_core.protobuf_helpers")
        ad_group_ad_service = client.get_service("AdGroupAdService")
        operation = client.get_type("AdGroupAdOperation")
        ad = operation.update
        ad.resource_name = ad_resource_name
        status_enum = client.enums.AdGroupAdStatusEnum
        ad.status = getattr(status_enum, new_status)
        client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, ad._pb),
        )
        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=customer_id,
            operations=[operation],
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads ad status update failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "adResourceName": getattr(result, "resource_name", ad_resource_name),
        "newStatus": new_status,
    }


def add_negative_keyword_to_campaign(
    config: dict[str, Any],
    *,
    campaign_name: str,
    keyword_text: str,
    match_type: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")

    # look up campaign resource name
    q = f"""
        SELECT campaign.id, campaign.resource_name, campaign.name
        FROM campaign
        WHERE campaign.name = '{_escape_gaql_string(campaign_name)}'
          AND campaign.status != 'REMOVED'
        LIMIT 1
    """
    try:
        response = google_ads_service.search(customer_id=customer_id, query=q)
        row = next(iter(response), None)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads campaign lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if row is None:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Campaign '{campaign_name}' was not found.",
            tool=tool,
            details={"campaignName": campaign_name},
        )

    campaign_resource_name = row.campaign.resource_name

    try:
        neg_criterion_service = client.get_service("CampaignCriterionService")
        operation = client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        criterion.campaign = campaign_resource_name
        criterion.negative = True
        criterion.keyword.text = keyword_text
        match_type_enum = client.enums.KeywordMatchTypeEnum
        criterion.keyword.match_type = getattr(match_type_enum, match_type.upper())
        response = neg_criterion_service.mutate_campaign_criteria(
            customer_id=customer_id,
            operations=[operation],
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads negative keyword add failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "campaignName": campaign_name,
        "campaignResourceName": campaign_resource_name,
        "negativeKeywordResourceName": getattr(result, "resource_name", None),
        "keywordText": keyword_text,
        "matchType": match_type.upper(),
    }


def create_responsive_search_ad(
    config: dict[str, Any],
    *,
    campaign_name: str,
    ad_group_name: str,
    headlines: list[str],
    descriptions: list[str],
    final_url: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")

    # look up ad group resource name
    q = f"""
        SELECT ad_group.id, ad_group.resource_name, ad_group.name, campaign.name
        FROM ad_group
        WHERE campaign.name = '{_escape_gaql_string(campaign_name)}'
          AND ad_group.name = '{_escape_gaql_string(ad_group_name)}'
          AND ad_group.status != 'REMOVED'
        LIMIT 1
    """
    try:
        response = google_ads_service.search(customer_id=customer_id, query=q)
        row = next(iter(response), None)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads ad group lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if row is None:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Ad group '{ad_group_name}' in campaign '{campaign_name}' was not found.",
            tool=tool,
            details={"adGroupName": ad_group_name, "campaignName": campaign_name},
        )

    ad_group_resource_name = row.ad_group.resource_name

    try:
        ad_group_ad_service = client.get_service("AdGroupAdService")
        operation = client.get_type("AdGroupAdOperation")
        ad_group_ad = operation.create
        ad_group_ad.ad_group = ad_group_resource_name
        ad_group_ad.status = client.enums.AdGroupAdStatusEnum.ENABLED

        rsa = ad_group_ad.ad.responsive_search_ad
        for text in headlines:
            headline = client.get_type("AdTextAsset")
            headline.text = text
            rsa.headlines.append(headline)
        for text in descriptions:
            description = client.get_type("AdTextAsset")
            description.text = text
            rsa.descriptions.append(description)

        ad_group_ad.ad.final_urls.append(final_url)

        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=customer_id,
            operations=[operation],
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads RSA creation failed.",
            tool=tool,
            retryable=False,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "adResourceName": getattr(result, "resource_name", None),
        "campaignName": campaign_name,
        "adGroupName": ad_group_name,
        "headlineCount": len(headlines),
        "descriptionCount": len(descriptions),
        "finalUrl": final_url,
    }


def mutate_campaign_bidding_strategy(
    config: dict[str, Any],
    *,
    campaign_resource_name: str,
    bidding_strategy: str,
    target_cpa_micros: int | None = None,
    target_roas: float | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)

    try:
        protobuf_helpers = import_module("google.api_core.protobuf_helpers")
        campaign_service = client.get_service("CampaignService")
        operation = client.get_type("CampaignOperation")
        campaign = operation.update
        campaign.resource_name = campaign_resource_name

        strategy = bidding_strategy.upper()
        if strategy == "MAXIMIZE_CLICKS":
            campaign.maximize_clicks.CopyFrom(client.get_type("MaximizeClicks"))
        elif strategy == "MAXIMIZE_CONVERSIONS":
            campaign.maximize_conversions.CopyFrom(client.get_type("MaximizeConversions"))
        elif strategy == "TARGET_CPA":
            tc = client.get_type("TargetCpa")
            if target_cpa_micros:
                tc.target_cpa_micros = int(target_cpa_micros)
            campaign.target_cpa.CopyFrom(tc)
        elif strategy == "TARGET_ROAS":
            tr = client.get_type("TargetRoas")
            if target_roas:
                tr.target_roas = float(target_roas)
            campaign.target_roas.CopyFrom(tr)
        elif strategy == "MANUAL_CPC":
            campaign.manual_cpc.CopyFrom(client.get_type("ManualCpc"))
        else:
            raise AdsMcpError(
                status_code=400,
                error_code="REQUEST_INVALID",
                message=f"Unknown bidding strategy '{bidding_strategy}'. Use: MAXIMIZE_CLICKS, MAXIMIZE_CONVERSIONS, TARGET_CPA, TARGET_ROAS, MANUAL_CPC.",
                tool=tool,
            )

        client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, campaign._pb),
        )
        response = campaign_service.mutate_campaigns(
            customer_id=customer_id,
            operations=[operation],
        )
    except AdsMcpError:
        raise
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads bidding strategy update failed.",
            tool=tool,
            retryable=False,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "campaignResourceName": getattr(result, "resource_name", campaign_resource_name),
        "newBiddingStrategy": strategy,
        "targetCpaMicros": target_cpa_micros,
        "targetRoas": target_roas,
    }
