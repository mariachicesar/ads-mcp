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


# ── organic insights ─────────────────────────────────────────────────────────

def _organic(monkeypatch, *, media=None, followers=None, fb=None, fb_error=None):
    monkeypatch.setattr(read, "load_meta_ads_config", lambda **kw: {"page_id": "p1"})
    monkeypatch.setattr(read, "get_instagram_user_id", lambda config, **kw: "ig1")
    monkeypatch.setattr(read, "get_instagram_media_insights", lambda config, **kw: media or [])
    monkeypatch.setattr(read, "get_instagram_online_followers", lambda config, **kw: followers)

    def page(config, **kw):
        if fb_error:
            raise fb_error
        return fb or []

    monkeypatch.setattr(read, "get_page_post_insights", page)
    return read.get_organic_insights(ToolRequest(businessKey="el-cuis", payload={"days": 30}), None)["data"]


def _utc_for_pacific(hour):
    from datetime import datetime
    from shared.scheduling import SCHEDULE_TZ
    offset = int(datetime.now(SCHEDULE_TZ).utcoffset().total_seconds() // 3600)
    return (hour - offset) % 24


def test_post_rows_normalized(monkeypatch):
    data = _organic(
        monkeypatch,
        media=[{"id": "m1", "caption": "Hook line\nrest", "media_product_type": "REELS",
                "timestamp": "2026-11-02T03:00:00+0000", "permalink": "https://i/m1",
                "like_count": "5", "comments_count": 2,
                "insights": {"reach": "120", "views": 300, "saved": "1", "shares": 0, "total_interactions": "9"}}],
        fb=[{"id": "f1", "message": "FB hook", "created_time": "2026-11-02T03:00:00+0000",
             "permalink_url": "https://f/1", "reactions": "4", "comments": 1, "shares": "2", "reach": "50"}],
    )
    post = data["instagram"]["posts"][0]
    assert post["hook"] == "Hook line" and post["reach"] == 120 and post["likes"] == 5
    assert post["publishedPacific"].startswith("2026-11-01T")
    assert isinstance(post["totalInteractions"], int)
    assert data["facebook"]["posts"][0]["reach"] == 50 and data["facebook"]["posts"][0]["shares"] == 2


def test_recommends_peak_half_hour_in_window(monkeypatch):
    followers = {_utc_for_pacific(18): 100, _utc_for_pacific(19): 100, _utc_for_pacific(20): 120}
    data = _organic(monkeypatch, followers=followers)
    assert data["recommendedWeekdayTime"] == "20:00"
    assert data["instagram"]["audienceByHourPacific"][20] == 120


def test_recommends_default_when_flat_or_missing(monkeypatch):
    flat = {_utc_for_pacific(18): 105, _utc_for_pacific(19): 100, _utc_for_pacific(20): 109}
    data = _organic(monkeypatch, followers=flat)
    assert data["recommendedWeekdayTime"] == "19:00" and data["recommendationReason"]
    missing = _organic(monkeypatch, followers=None)
    assert missing["recommendedWeekdayTime"] == "19:00"
    assert "No audience-by-hour data" in missing["recommendationReason"]
    assert missing["instagram"]["audienceByHourPacific"] is None


def test_metric_error_becomes_warning(monkeypatch):
    error = AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message="rejected", tool="t")
    data = _organic(monkeypatch, media=[{"id": "m1", "caption": "x", "insights": {"reach": 1}}], fb_error=error)
    assert len(data["instagram"]["posts"]) == 1
    assert data["facebook"]["posts"] == []
    assert any("Facebook" in w for w in data["warnings"])


def test_per_media_insight_failure_gives_nulls_and_one_warning(monkeypatch):
    media = [{"id": f"m{i}", "caption": "x", "insights": None, "insightsError": "bad metric"} for i in range(2)]
    data = _organic(monkeypatch, media=media, followers={1: 1})
    assert all(p["reach"] is None for p in data["instagram"]["posts"])
    assert sum("rejected insight metrics" in w for w in data["warnings"]) == 1
