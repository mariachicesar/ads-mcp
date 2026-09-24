from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared import google_ads_client
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.service_import import load_service_module

write = load_service_module("google-ads", "tools.write")

CONFIG = {"customer_account_id": "2943425139"}


def _enum(name):
    return SimpleNamespace(name=name)


# --- shared.google_ads_client helpers -------------------------------------

class _FakeSearchService:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def search(self, *, customer_id, query):
        self.queries.append(query)
        return iter(self.rows)


class _FakeMask:
    def __init__(self):
        self.paths = []


class _FakeOperation:
    def __init__(self):
        self.update = SimpleNamespace(resource_name=None, value_settings=SimpleNamespace())
        self.update_mask = _FakeMask()


class _FakeConversionService:
    def __init__(self):
        self.calls = []

    def mutate_conversion_actions(self, *, customer_id, operations):
        self.calls.append((customer_id, operations))
        return SimpleNamespace(results=[SimpleNamespace(resource_name=operations[0].update.resource_name)])


class _FakeClient:
    def __init__(self, rows=()):
        self.search_service = _FakeSearchService(list(rows))
        self.conversion_service = _FakeConversionService()
        self.enums = SimpleNamespace(ConversionActionStatusEnum=SimpleNamespace(ENABLED="ENUM_ENABLED", HIDDEN="ENUM_HIDDEN"))

    def get_service(self, name):
        return {"GoogleAdsService": self.search_service, "ConversionActionService": self.conversion_service}[name]

    def get_type(self, name):
        assert name == "ConversionActionOperation"
        return _FakeOperation()


def _row(name="booking_confirmed", type_name="GOOGLE_ANALYTICS_4_CUSTOM"):
    return SimpleNamespace(conversion_action=SimpleNamespace(
        resource_name="customers/2943425139/conversionActions/1",
        id=1,
        name=name,
        status=_enum("HIDDEN"),
        type_=_enum(type_name),
        primary_for_goal=False,
        phone_call_duration_seconds=0,
        value_settings=SimpleNamespace(default_value=0.0, always_use_default_value=False),
    ))


def test_conversion_snapshot_returns_normalized_fields(monkeypatch):
    client = _FakeClient([_row()])
    monkeypatch.setattr(google_ads_client, "build_google_ads_client", lambda config, tool=None: client)

    snapshot = google_ads_client.get_conversion_action_snapshot(CONFIG, conversion_name="booking_confirmed")

    assert "conversion_action.name = 'booking_confirmed'" in client.search_service.queries[0]
    assert snapshot == {
        "customerAccountId": "2943425139",
        "resourceName": "customers/2943425139/conversionActions/1",
        "conversionId": "1",
        "name": "booking_confirmed",
        "status": "HIDDEN",
        "type": "GOOGLE_ANALYTICS_4_CUSTOM",
        "primaryForGoal": False,
        "phoneCallDurationSeconds": 0,
        "defaultValue": 0.0,
        "alwaysUseDefaultValue": False,
    }


def test_conversion_snapshot_rejects_missing_name(monkeypatch):
    monkeypatch.setattr(google_ads_client, "build_google_ads_client", lambda config, tool=None: _FakeClient([]))

    with pytest.raises(AdsMcpError) as exc:
        google_ads_client.get_conversion_action_snapshot(CONFIG, conversion_name="nope")

    assert exc.value.status_code == 404
    assert exc.value.error_code == "REQUEST_INVALID"


def test_conversion_snapshot_rejects_duplicate_names(monkeypatch):
    monkeypatch.setattr(google_ads_client, "build_google_ads_client", lambda config, tool=None: _FakeClient([_row(), _row()]))

    with pytest.raises(AdsMcpError) as exc:
        google_ads_client.get_conversion_action_snapshot(CONFIG, conversion_name="booking_confirmed")

    assert exc.value.status_code == 409
    assert exc.value.error_code == "REQUEST_INVALID"
    assert "2 conversion actions" in exc.value.message


def test_mutate_conversion_action_sends_explicit_mask_including_false(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(google_ads_client, "build_google_ads_client", lambda config, tool=None: client)

    google_ads_client.mutate_conversion_action(
        CONFIG,
        resource_name="customers/2943425139/conversionActions/1",
        updates={"status": "ENABLED", "primary_for_goal": False, "value_settings.default_value": 0.0},
    )

    customer_id, operations = client.conversion_service.calls[0]
    op = operations[0]
    assert customer_id == "2943425139"
    assert op.update.status == "ENUM_ENABLED"
    assert op.update.primary_for_goal is False
    assert op.update.value_settings.default_value == 0.0
    assert op.update_mask.paths == ["status", "primary_for_goal", "value_settings.default_value"]


def test_mutate_conversion_action_rejects_unknown_path(monkeypatch):
    monkeypatch.setattr(google_ads_client, "build_google_ads_client", lambda config, tool=None: _FakeClient())

    with pytest.raises(AdsMcpError) as exc:
        google_ads_client.mutate_conversion_action(CONFIG, resource_name="x", updates={"category": "LEAD"})

    assert exc.value.error_code == "REQUEST_INVALID"


# --- tools.write.update_conversion_action ---------------------------------

def _snapshot(**overrides):
    base = {
        "customerAccountId": "2943425139",
        "resourceName": "customers/2943425139/conversionActions/1",
        "conversionId": "1",
        "name": "booking_confirmed",
        "status": "HIDDEN",
        "type": "GOOGLE_ANALYTICS_4_CUSTOM",
        "primaryForGoal": False,
        "phoneCallDurationSeconds": 0,
        "defaultValue": 0.0,
        "alwaysUseDefaultValue": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def patched(monkeypatch):
    state = {"snapshot": _snapshot(), "mutations": []}
    monkeypatch.setattr(write, "load_google_ads_sdk_config", lambda **kw: CONFIG)
    monkeypatch.setattr(write, "get_conversion_action_snapshot", lambda config, **kw: state["snapshot"])

    def fake_mutate(config, *, resource_name, updates, tool=None):
        state["mutations"].append((resource_name, updates))
        return {"customerAccountId": "2943425139", "resourceName": resource_name, "updatedFields": list(updates)}

    monkeypatch.setattr(write, "mutate_conversion_action", fake_mutate)
    return state


def _request(dry_run=True, approval_id=None, **payload):
    return ToolRequest(businessKey="el-cuis", dryRun=dry_run, approvalId=approval_id,
                       payload={"conversionName": "booking_confirmed", **payload})


def test_dry_run_proposes_changes_without_mutating(patched):
    response = write.update_conversion_action(_request(status="ENABLED", primaryForGoal=True, defaultValue=50), None)

    assert response["mode"] == "dry-run"
    assert response["requiresConfirmation"] is True
    assert response["approvalId"]
    assert [(c["field"], c["before"], c["after"]) for c in response["changes"]] == [
        ("conversion_action.status", "HIDDEN", "ENABLED"),
        ("conversion_action.primary_for_goal", False, True),
        ("conversion_action.value_settings.default_value", 0.0, 50.0),
        ("conversion_action.value_settings.always_use_default_value", False, True),
    ]
    assert patched["mutations"] == []


def test_execute_without_approval_is_blocked(patched):
    with pytest.raises(AdsMcpError) as exc:
        write.update_conversion_action(_request(dry_run=False, status="ENABLED"), None)

    assert exc.value.error_code == "BUSINESS_RULE_BLOCKED"
    assert patched["mutations"] == []


def test_execute_sends_only_changed_fields(patched):
    patched["snapshot"] = _snapshot(status="ENABLED")

    response = write.update_conversion_action(
        _request(dry_run=False, approval_id="ok", status="ENABLED", primaryForGoal=True, defaultValue=50), None)

    assert response["executed"] is True
    assert patched["mutations"] == [(
        "customers/2943425139/conversionActions/1",
        {"primary_for_goal": True, "value_settings.default_value": 50.0, "value_settings.always_use_default_value": True},
    )]
    assert all(c["status"] == "applied" for c in response["changes"])


def test_demoting_to_secondary_is_a_real_change(patched):
    patched["snapshot"] = _snapshot(name="phone_click", status="ENABLED", primaryForGoal=True)

    write.update_conversion_action(_request(dry_run=False, approval_id="ok", primaryForGoal=False), None)

    assert patched["mutations"][0][1] == {"primary_for_goal": False}


def test_already_matching_is_a_no_op(patched):
    patched["snapshot"] = _snapshot(status="ENABLED", primaryForGoal=True, defaultValue=50.0, alwaysUseDefaultValue=True)

    response = write.update_conversion_action(
        _request(dry_run=False, approval_id="ok", status="ENABLED", primaryForGoal=True, defaultValue=50), None)

    assert response["changes"] == []
    assert response["executed"] is False
    assert response["requiresConfirmation"] is False
    assert patched["mutations"] == []


def test_call_duration_only_on_call_conversions(patched):
    with pytest.raises(AdsMcpError) as exc:
        write.update_conversion_action(_request(phoneCallDurationSeconds=60), None)

    assert exc.value.error_code == "REQUEST_INVALID"
    assert "AD_CALL" in exc.value.message


def test_call_duration_on_ad_call(patched):
    patched["snapshot"] = _snapshot(name="Calls from ads", type="AD_CALL", status="ENABLED", primaryForGoal=True,
                                    phoneCallDurationSeconds=0, defaultValue=1.0)

    response = write.update_conversion_action(_request(phoneCallDurationSeconds=60, defaultValue=5), None)

    assert ("conversion_action.phone_call_duration_seconds", 0, 60) in [
        (c["field"], c["before"], c["after"]) for c in response["changes"]]


@pytest.mark.parametrize("payload, fragment", [
    ({}, "at least one"),
    ({"status": "PAUSED"}, "status"),
    ({"defaultValue": -1}, "defaultValue"),
    ({"defaultValue": "abc"}, "defaultValue"),
    ({"phoneCallDurationSeconds": -5}, "phoneCallDurationSeconds"),
])
def test_invalid_inputs(patched, payload, fragment):
    with pytest.raises(AdsMcpError) as exc:
        write.update_conversion_action(_request(**payload), None)

    assert exc.value.error_code == "REQUEST_INVALID"
    assert fragment in exc.value.message


def test_missing_conversion_name(patched):
    with pytest.raises(AdsMcpError) as exc:
        write.update_conversion_action(ToolRequest(businessKey="el-cuis", payload={"status": "ENABLED"}), None)

    assert exc.value.error_code == "REQUEST_INVALID"
