from shared.google_ads_client import list_accessible_customers
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


def get_campaign_performance(request: ToolRequest, request_id: str | None) -> dict:
    config = load_google_ads_config(
        business_key=request.businessKey,
        tool="get_campaign_performance",
    )
    return build_success_response(
        service=SERVICE_NAME,
        tool="get_campaign_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary="Loaded Google Ads configuration for campaign performance lookup.",
        data={
            "rows": [],
            "payload": request.payload,
            "accountContext": _build_account_context(config),
        },
        freshness={"state": "live"},
    )
