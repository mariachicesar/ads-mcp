from __future__ import annotations

from shared import meta_ads_client
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.runtime_config import load_meta_ads_config
from tests.service_import import load_service_module

read = load_service_module("meta-ads", "tools.read")


def test_list_ad_accounts_returns_configured_account(monkeypatch):
    class Account:
        def api_get(self, *, fields):
            assert fields == ["id", "name", "account_status", "currency", "timezone_name"]
            return {"id": "act_123", "name": "Demo", "account_status": 1, "currency": "USD", "timezone_name": "America/Los_Angeles"}

    monkeypatch.setattr(meta_ads_client, "get_ad_account", lambda config, tool: Account())

    accounts = meta_ads_client.list_ad_accounts(
        {"ad_account_id": "123", "access_token": "token"}, tool="list_ad_accounts"
    )

    assert accounts == [{
        "adAccountId": "act_123", "name": "Demo", "status": 1, "currency": "USD",
        "timezoneName": "America/Los_Angeles", "isConfiguredAccount": True,
    }]


def test_get_campaign_performance_maps_date_range_and_normalizes_rows(monkeypatch):
    monkeypatch.setattr(read, "load_meta_ads_config", lambda **kwargs: {"ad_account_id": "123"})
    captured = {}

    def fetch(config, **kwargs):
        captured.update(kwargs)
        return [{
            "campaign_id": "1", "campaign_name": "Leads", "impressions": "1000",
            "clicks": "25", "spend": "18.50", "ctr": "2.5", "cpc": "0.74",
            "reach": "800", "frequency": "1.25",
            "actions": [{"action_type": "lead", "value": "4"}],
            "cost_per_action_type": [{"action_type": "lead", "value": "4.63"}],
        }]

    monkeypatch.setattr(read, "get_campaign_insights_snapshot", fetch)
    read.LEVEL_FETCHERS["campaign"] = fetch
    try:
        response = read.get_campaign_performance(
            ToolRequest(businessKey="acme", payload={"dateRange": "LAST_7_DAYS", "campaignIds": ["1"]}), None
        )
    finally:
        read.LEVEL_FETCHERS["campaign"] = read.get_campaign_insights_snapshot

    assert captured["date_preset"] == "last_7d"
    assert captured["campaign_ids"] == ["1"]
    assert response["data"]["rows"][0]["spend"] == 18.5
    assert response["data"]["rows"][0]["conversionActions"] == [{
        "actionType": "lead", "value": 4.0, "costPerAction": 4.63,
    }]
    assert response["data"]["summary"] == {"impressions": 1000, "clicks": 25, "spend": 18.5}


def test_publisher_platform_breakdown_uses_campaign_level_by_default(monkeypatch):
    monkeypatch.setattr(read, "load_meta_ads_config", lambda **kwargs: {"ad_account_id": "123"})
    captured = {}

    def fetch(config, **kwargs):
        captured.update(kwargs)
        return [{"campaign_id": "1", "campaign_name": "Leads", "publisher_platform": "instagram"}]

    monkeypatch.setattr(read, "fetch_publisher_platform_breakdown", fetch)
    response = read.get_publisher_platform_breakdown(ToolRequest(businessKey="acme"), None)

    assert captured == {"level": "campaign", "object_ids": None, "date_preset": "last_30d", "tool": "get_publisher_platform_breakdown"}
    assert response["data"]["rows"][0]["publisher_platform"] == "instagram"


def test_meta_ads_config_requires_full_credential_shape(clients_dir, fake_secrets):
    (clients_dir / "acme.json").write_text(
        '{"businessKey":"acme","displayName":"Acme","platforms":{"meta-ads":{"enabled":true}}}'
    )
    fake_secrets.store["/ads-mcp/acme/meta-ads/config"] = {
        "ad_account_id": "123", "access_token": "token",
    }

    with __import__("pytest").raises(AdsMcpError) as exc:
        load_meta_ads_config(business_key="acme")

    assert exc.value.error_code == "PLATFORM_CONFIG_INCOMPLETE"
    assert set(exc.value.details["missingKeys"]) == {"app_id", "app_secret", "page_id"}