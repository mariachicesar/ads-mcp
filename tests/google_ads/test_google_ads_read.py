from __future__ import annotations

from types import SimpleNamespace

from shared.models import ToolRequest
from tests.service_import import load_service_module

read = load_service_module("google-ads", "tools.read")


def _enum(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


class _FakeGoogleAdsService:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    def search(self, *, customer_id, query):
        self.queries.append(query)
        return iter(self.rows)


class _FakeClient:
    def __init__(self, service):
        self.service = service

    def get_service(self, name):
        assert name == "GoogleAdsService"
        return self.service


def _patch_client(monkeypatch, rows):
    service = _FakeGoogleAdsService(rows)
    monkeypatch.setattr(read, "load_google_ads_sdk_config", lambda **kw: {"customer_account_id": "123"})
    monkeypatch.setattr(read, "_resolve_working_client", lambda config, tool: (_FakeClient(service), "123"))
    return service


def test_get_conversion_actions_uses_gaql_type_field_and_returns_goal_fields(monkeypatch):
    row = SimpleNamespace(conversion_action=SimpleNamespace(
        id=7790452537,
        name="Calls from ads",
        status=_enum("ENABLED"),
        type_=_enum("AD_CALL"),
        category=_enum("PHONE_CALL_LEAD"),
        counting_type=_enum("MANY_PER_CLICK"),
        include_in_conversions_metric=True,
        primary_for_goal=True,
        phone_call_duration_seconds=60,
        value_settings=SimpleNamespace(default_value=1.0),
    ))
    service = _patch_client(monkeypatch, [row])

    response = read.get_conversion_actions(ToolRequest(businessKey="el-cuis"), None)

    query = service.queries[0]
    assert "conversion_action.type," in query
    assert "type_" not in query
    assert "conversion_action.primary_for_goal" in query
    assert "conversion_action.phone_call_duration_seconds" in query
    assert response["data"]["rows"] == [{
        "conversion_id": "7790452537",
        "name": "Calls from ads",
        "status": "ENABLED",
        "type": "AD_CALL",
        "category": "PHONE_CALL_LEAD",
        "counting_type": "MANY_PER_CLICK",
        "included_in_conversions": True,
        "primary_for_goal": True,
        "phone_call_duration_seconds": 60,
        "default_value": 1.0,
    }]


def test_change_history_rejects_ranges_older_than_30_days(monkeypatch):
    service = _patch_client(monkeypatch, [])

    for requested in ("LAST_30_DAYS", "LAST_MONTH", "BOGUS"):
        read.get_change_history(ToolRequest(businessKey="el-cuis", payload={"dateRange": requested}), None)
    read.get_change_history(ToolRequest(businessKey="el-cuis", payload={"dateRange": "THIS_MONTH"}), None)

    assert all("DURING LAST_14_DAYS" in q for q in service.queries[:3])
    assert "DURING THIS_MONTH" in service.queries[3]


def test_change_history_summary_reads_cleanly(monkeypatch):
    _patch_client(monkeypatch, [])

    response = read.get_change_history(ToolRequest(businessKey="el-cuis", payload={"dateRange": "LAST_7_DAYS"}), None)

    assert response["summary"] == "0 changes (last 7 days)."
