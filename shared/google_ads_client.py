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
