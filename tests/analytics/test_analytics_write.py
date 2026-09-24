from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.api_core import exceptions as google_exceptions
from google.analytics.admin_v1beta.types import KeyEvent

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.service_import import load_service_module

write = load_service_module("analytics", "tools.write")

GA4_CONFIG = {"property_id": "554624356", "client_id": "id", "client_secret": "secret", "refresh_token": "rt"}


def _key_event(name, method="ONCE_PER_SESSION"):
    return SimpleNamespace(event_name=name, counting_method=SimpleNamespace(name=method),
                           name=f"properties/554624356/keyEvents/{name}")


class _FakeAdmin:
    def __init__(self):
        self.key_events = []
        self.links = []
        self.created = []
        self.fail_with = None

    def list_key_events(self, *, parent):
        assert parent == "properties/554624356"
        return list(self.key_events)

    def create_key_event(self, *, parent, key_event):
        if self.fail_with:
            raise self.fail_with
        self.created.append((parent, key_event))
        return SimpleNamespace(name=f"{parent}/keyEvents/1")

    def list_google_ads_links(self, *, parent):
        return list(self.links)

    def create_google_ads_link(self, *, parent, google_ads_link):
        if self.fail_with:
            raise self.fail_with
        self.created.append((parent, google_ads_link))
        return SimpleNamespace(name=f"{parent}/googleAdsLinks/9", customer_id=google_ads_link.customer_id)


@pytest.fixture
def admin(monkeypatch):
    fake = _FakeAdmin()
    monkeypatch.setattr(write, "load_platform_runtime_config", lambda **kw: GA4_CONFIG)
    monkeypatch.setattr(write, "_build_admin_client", lambda config: fake)
    return fake


def _ke_request(event_name="booking_confirmed", dry_run=True, approval_id=None, **extra):
    return ToolRequest(businessKey="el-cuis", dryRun=dry_run, approvalId=approval_id,
                       payload={"eventName": event_name, **extra})


def test_key_event_dry_run_lists_existing_and_proposes(admin):
    admin.key_events = [_key_event("purchase")]

    response = write.create_key_event(_ke_request(), None)

    assert response["requiresConfirmation"] is True
    assert response["data"]["existingKeyEvents"] == ["purchase"]
    assert response["changes"][0]["after"] == {"eventName": "booking_confirmed", "countingMethod": "ONCE_PER_SESSION"}
    assert admin.created == []


def test_key_event_execute_requires_approval(admin):
    with pytest.raises(AdsMcpError) as exc:
        write.create_key_event(_ke_request(dry_run=False), None)

    assert exc.value.error_code == "BUSINESS_RULE_BLOCKED"
    assert admin.created == []


def test_key_event_execute_creates_once_per_session(admin):
    response = write.create_key_event(_ke_request(dry_run=False, approval_id="ok"), None)

    parent, key_event = admin.created[0]
    assert parent == "properties/554624356"
    assert key_event.event_name == "booking_confirmed"
    assert key_event.counting_method == KeyEvent.CountingMethod.ONCE_PER_SESSION
    assert response["executed"] is True


def test_existing_key_event_is_no_op(admin):
    admin.key_events = [_key_event("booking_confirmed")]

    response = write.create_key_event(_ke_request(dry_run=False, approval_id="ok"), None)

    assert response["changes"] == [] and response["executed"] is False
    assert "warnings" not in response
    assert admin.created == []


def test_existing_key_event_with_other_counting_method_warns(admin):
    admin.key_events = [_key_event("booking_confirmed", "ONCE_PER_EVENT")]

    response = write.create_key_event(_ke_request(), None)

    assert response["changes"] == []
    assert "ONCE_PER_EVENT" in response["warnings"][0]


@pytest.mark.parametrize("name", ["", "1booking", "booking-confirmed", "a" * 41])
def test_key_event_rejects_invalid_names(admin, name):
    with pytest.raises(AdsMcpError) as exc:
        write.create_key_event(_ke_request(event_name=name), None)

    assert exc.value.error_code == "REQUEST_INVALID"


def test_key_event_rejects_unknown_counting_method(admin):
    with pytest.raises(AdsMcpError) as exc:
        write.create_key_event(_ke_request(countingMethod="ALWAYS"), None)

    assert exc.value.error_code == "REQUEST_INVALID"


def test_create_key_event_maps_insufficient_scope(admin):
    admin.fail_with = google_exceptions.PermissionDenied("Request had insufficient authentication scopes.")

    with pytest.raises(AdsMcpError) as exc:
        write.create_key_event(_ke_request(dry_run=False, approval_id="ok"), None)

    assert exc.value.status_code == 403
    assert exc.value.error_code == "AUTH_SCOPE_MISSING"
    assert "get-refresh-token.py" in exc.value.message


def test_other_upstream_errors_map_to_upstream_error(admin):
    admin.fail_with = google_exceptions.InternalServerError("boom")

    with pytest.raises(AdsMcpError) as exc:
        write.create_key_event(_ke_request(dry_run=False, approval_id="ok"), None)

    assert exc.value.error_code == "UPSTREAM_ERROR"
    assert exc.value.retryable is True


def test_non_scope_permission_denied_is_not_retryable(admin):
    admin.fail_with = google_exceptions.PermissionDenied(
        "Google Analytics Admin API has not been used in project 1 before or it is disabled. SERVICE_DISABLED"
    )

    with pytest.raises(AdsMcpError) as exc:
        write.create_key_event(_ke_request(dry_run=False, approval_id="ok"), None)

    assert exc.value.status_code == 403
    assert exc.value.error_code == "UPSTREAM_PERMISSION_DENIED"
    assert exc.value.retryable is False


@pytest.fixture
def ads_config(monkeypatch):
    monkeypatch.setattr(write, "load_google_ads_config", lambda **kw: {"customer_account_id": "294-342-5139"})


def _link_request(dry_run=True, approval_id=None):
    return ToolRequest(businessKey="el-cuis", dryRun=dry_run, approvalId=approval_id)


def test_link_dry_run_uses_clients_own_customer_id(admin, ads_config):
    admin.links = [SimpleNamespace(customer_id="1111111111")]

    response = write.link_google_ads(_link_request(), None)

    assert response["requiresConfirmation"] is True
    assert response["changes"][0]["after"] == "2943425139"
    assert response["data"]["linkedCustomerIds"] == ["1111111111"]
    assert response["ruleChecks"][0]["rule"] == "ads-link-same-client"
    assert admin.created == []


def test_link_execute_requires_approval(admin, ads_config):
    with pytest.raises(AdsMcpError) as exc:
        write.link_google_ads(_link_request(dry_run=False), None)

    assert exc.value.error_code == "BUSINESS_RULE_BLOCKED"


def test_link_execute_creates_link(admin, ads_config):
    response = write.link_google_ads(_link_request(dry_run=False, approval_id="ok"), None)

    parent, link = admin.created[0]
    assert parent == "properties/554624356"
    assert link.customer_id == "2943425139"
    assert response["executed"] is True


def test_link_already_present_is_no_op(admin, ads_config):
    admin.links = [SimpleNamespace(customer_id="2943425139")]

    response = write.link_google_ads(_link_request(dry_run=False, approval_id="ok"), None)

    assert response["changes"] == [] and response["executed"] is False
    assert admin.created == []


def test_link_without_google_ads_platform_fails(admin, monkeypatch):
    def missing(**kw):
        raise AdsMcpError(status_code=404, error_code="PLATFORM_NOT_CONFIGURED", message="nope")

    monkeypatch.setattr(write, "load_google_ads_config", missing)

    with pytest.raises(AdsMcpError) as exc:
        write.link_google_ads(_link_request(), None)

    assert exc.value.error_code == "PLATFORM_NOT_CONFIGURED"


def test_link_scope_error(admin, ads_config):
    admin.fail_with = google_exceptions.PermissionDenied("Request had insufficient authentication scopes.")

    with pytest.raises(AdsMcpError) as exc:
        write.link_google_ads(_link_request(dry_run=False, approval_id="ok"), None)

    assert exc.value.error_code == "AUTH_SCOPE_MISSING"
