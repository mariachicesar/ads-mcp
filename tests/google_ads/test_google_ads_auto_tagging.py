from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared import google_ads_client
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.service_import import load_service_module

write = load_service_module("google-ads", "tools.write")

CONFIG = {"customer_account_id": "2943425139"}


class _FakeMask:
    def __init__(self):
        self.paths = []


class _FakeCustomerOperation:
    def __init__(self):
        self.update = SimpleNamespace(resource_name=None, auto_tagging_enabled=None)
        self.update_mask = _FakeMask()


class _FakeClient:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.mutations = []
        client = self

        class _Search:
            def search(self, *, customer_id, query):
                client.query = query
                return iter(client.rows)

        class _Customers:
            def mutate_customer(self, *, customer_id, operation):
                client.mutations.append((customer_id, operation))
                return SimpleNamespace(result=SimpleNamespace(resource_name=operation.update.resource_name))

        self.services = {"GoogleAdsService": _Search(), "CustomerService": _Customers()}

    def get_service(self, name):
        return self.services[name]

    def get_type(self, name):
        assert name == "CustomerOperation"
        return _FakeCustomerOperation()


def test_customer_snapshot(monkeypatch):
    row = SimpleNamespace(customer=SimpleNamespace(resource_name="customers/2943425139", auto_tagging_enabled=False))
    client = _FakeClient([row])
    monkeypatch.setattr(google_ads_client, "build_google_ads_client", lambda config, tool=None: client)

    snapshot = google_ads_client.get_customer_settings_snapshot(CONFIG)

    assert "customer.auto_tagging_enabled" in client.query
    assert snapshot == {"customerAccountId": "2943425139", "resourceName": "customers/2943425139", "autoTaggingEnabled": False}


def test_mutate_auto_tagging_sets_explicit_mask(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(google_ads_client, "build_google_ads_client", lambda config, tool=None: client)

    google_ads_client.mutate_customer_auto_tagging(CONFIG, resource_name="customers/2943425139", enabled=False)

    customer_id, op = client.mutations[0]
    assert customer_id == "2943425139"
    assert op.update.auto_tagging_enabled is False
    assert op.update_mask.paths == ["auto_tagging_enabled"]


@pytest.fixture
def patched(monkeypatch):
    state = {"enabled": False, "mutations": []}
    monkeypatch.setattr(write, "load_google_ads_sdk_config", lambda **kw: CONFIG)
    monkeypatch.setattr(write, "get_customer_settings_snapshot", lambda config, **kw: {
        "customerAccountId": "2943425139", "resourceName": "customers/2943425139", "autoTaggingEnabled": state["enabled"]})

    def fake_mutate(config, *, resource_name, enabled, tool=None):
        state["mutations"].append(enabled)
        return {"customerAccountId": "2943425139", "resourceName": resource_name, "autoTaggingEnabled": enabled}

    monkeypatch.setattr(write, "mutate_customer_auto_tagging", fake_mutate)
    return state


def _request(enabled=True, dry_run=True, approval_id=None):
    return ToolRequest(businessKey="el-cuis", dryRun=dry_run, approvalId=approval_id, payload={"enabled": enabled})


def test_dry_run_proposes(patched):
    response = write.set_auto_tagging(_request(), None)

    assert response["requiresConfirmation"] is True
    assert [(c["field"], c["before"], c["after"]) for c in response["changes"]] == [
        ("customer.auto_tagging_enabled", False, True)]
    assert patched["mutations"] == []


def test_execute_requires_approval(patched):
    with pytest.raises(AdsMcpError) as exc:
        write.set_auto_tagging(_request(dry_run=False), None)

    assert exc.value.error_code == "BUSINESS_RULE_BLOCKED"


def test_execute_applies(patched):
    response = write.set_auto_tagging(_request(dry_run=False, approval_id="ok"), None)

    assert response["executed"] is True
    assert patched["mutations"] == [True]


def test_already_enabled_is_no_op(patched):
    patched["enabled"] = True

    response = write.set_auto_tagging(_request(dry_run=False, approval_id="ok"), None)

    assert response["changes"] == [] and response["executed"] is False
    assert patched["mutations"] == []


@pytest.mark.parametrize("value", [None, "yes", 1])
def test_enabled_must_be_bool(patched, value):
    with pytest.raises(AdsMcpError) as exc:
        write.set_auto_tagging(_request(enabled=value), None)

    assert exc.value.error_code == "REQUEST_INVALID"
