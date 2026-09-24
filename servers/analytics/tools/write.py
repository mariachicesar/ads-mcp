"""GA4 Analytics write tools — GA4 Admin API (v1beta).

These need the analytics.edit OAuth scope. The read-only token used by the
read tools gets a 403 on writes, which surfaces as AUTH_SCOPE_MISSING.
"""

from __future__ import annotations

import re
from typing import Any

from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.analytics.admin_v1beta.types import GoogleAdsLink, KeyEvent
from google.api_core import exceptions as google_exceptions
from google.oauth2.credentials import Credentials

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_change, build_rule_check, build_success_response
from shared.runtime_config import load_google_ads_config, load_platform_runtime_config

SERVICE_NAME = "analytics"
_REQUIRED_KEYS = ("property_id", "client_id", "client_secret", "refresh_token")
_COUNTING_METHODS = {"ONCE_PER_EVENT", "ONCE_PER_SESSION"}
# GA4 event names: start with a letter; letters, digits, underscores; max 40.
_EVENT_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")


def _load_analytics_config(business_key: str, tool: str) -> dict[str, Any]:
    return load_platform_runtime_config(
        platform="analytics",
        business_key=business_key,
        required_keys=_REQUIRED_KEYS,
        tool=tool,
    )


def _build_admin_client(config: dict[str, Any]) -> AnalyticsAdminServiceClient:
    creds = Credentials(
        token=None,
        refresh_token=config["refresh_token"],
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    return AnalyticsAdminServiceClient(credentials=creds)


def _property(config: dict[str, Any]) -> str:
    return f"properties/{config['property_id']}"


def _upstream_error(exc: Exception, *, tool: str, action: str) -> AdsMcpError:
    if isinstance(exc, google_exceptions.PermissionDenied) and "scope" in str(exc).lower():
        return AdsMcpError(
            status_code=403,
            error_code="AUTH_SCOPE_MISSING",
            message=(
                "The GA4 token for this client lacks the analytics.edit scope. Re-run "
                "scripts/get-refresh-token.py (it requests analytics.edit) and store the new "
                "refresh token in this client's analytics secret."
            ),
            tool=tool,
            details={"reason": str(exc)},
        )
    return AdsMcpError(
        status_code=502,
        error_code="UPSTREAM_ERROR",
        message=f"GA4 Admin {action} failed.",
        tool=tool,
        retryable=True,
        details={"reason": str(exc)},
    )


def _require_approval(request: ToolRequest, tool: str, rule_checks: list[dict[str, Any]]) -> None:
    if not request.approvalId:
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="approvalId is required for execute requests.",
            rule_checks=rule_checks,
            tool=tool,
        )
    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            rule_checks=rule_checks,
            tool=tool,
        )


def _request_error(message: str, tool: str) -> AdsMcpError:
    return AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message=message, tool=tool)


def create_key_event(request: ToolRequest, request_id: str | None) -> dict:
    tool = "create_key_event"
    payload = request.payload or {}
    event_name = str(payload.get("eventName") or "").strip()
    counting_method = str(payload.get("countingMethod") or "ONCE_PER_SESSION").upper()

    if not _EVENT_NAME_RE.fullmatch(event_name):
        raise _request_error(
            "eventName must start with a letter and use only letters, digits and underscores "
            "(max 40 characters).",
            tool,
        )
    if counting_method not in _COUNTING_METHODS:
        raise _request_error("countingMethod must be ONCE_PER_SESSION or ONCE_PER_EVENT.", tool)

    rule_checks = [
        build_rule_check(
            rule="ga4-protected-resources",
            passed=True,
            message="CLAUDE.md lists no protected GA4 resources.",
        )
    ]

    config = _load_analytics_config(request.businessKey, tool)
    client = _build_admin_client(config)
    parent = _property(config)

    try:
        existing = list(client.list_key_events(parent=parent))
    except Exception as exc:
        raise _upstream_error(exc, tool=tool, action="key event lookup") from exc

    data = {
        "propertyId": str(config["property_id"]),
        "existingKeyEvents": sorted(k.event_name for k in existing),
    }
    is_dry_run = request.dryRun is not False
    match = next((k for k in existing if k.event_name == event_name), None)

    if match is not None:
        warnings = []
        if match.counting_method.name != counting_method:
            warnings.append(
                f"{event_name} already exists with counting method {match.counting_method.name}; "
                "this tool does not change existing key events. Change it in GA4 Admin if needed."
            )
        return build_success_response(
            service=SERVICE_NAME,
            tool=tool,
            mode="dry-run" if is_dry_run else "execute",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"No change: {event_name} is already a key event.",
            rule_checks=rule_checks,
            changes=[],
            data=data,
            requires_confirmation=False,
            executed=False,
            warnings=warnings,
        )

    changes = [
        build_change(
            field="key_event",
            label="Key event",
            before=None,
            after={"eventName": event_name, "countingMethod": counting_method},
            status="proposed",
            resource_type="key_event",
        )
    ]

    if is_dry_run:
        return build_success_response(
            service=SERVICE_NAME,
            tool=tool,
            mode="dry-run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would mark {event_name} as a key event ({counting_method}).",
            rule_checks=rule_checks,
            changes=changes,
            data=data,
            requires_confirmation=True,
            executed=False,
        )

    _require_approval(request, tool, rule_checks)

    try:
        created = client.create_key_event(
            parent=parent,
            key_event=KeyEvent(
                event_name=event_name,
                counting_method=KeyEvent.CountingMethod[counting_method],
            ),
        )
    except Exception as exc:
        raise _upstream_error(exc, tool=tool, action="key event creation") from exc

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool=tool,
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Marked {event_name} as a key event ({counting_method}).",
        rule_checks=rule_checks,
        changes=changes,
        data={**data, "keyEventResourceName": created.name},
        requires_confirmation=False,
        executed=True,
    )


def link_google_ads(request: ToolRequest, request_id: str | None) -> dict:
    tool = "link_google_ads"
    # The customer ID comes only from this client's own manifest, so a call can
    # never link GA4 to another client's Ads account.
    ads_config = load_google_ads_config(business_key=request.businessKey, tool=tool)
    customer_id = str(ads_config["customer_account_id"]).replace("-", "").strip()

    rule_checks = [
        build_rule_check(
            rule="ads-link-same-client",
            passed=True,
            message=f"Links only to {customer_id}, the Google Ads account in {request.businessKey}'s own manifest.",
        )
    ]

    config = _load_analytics_config(request.businessKey, tool)
    client = _build_admin_client(config)
    parent = _property(config)

    try:
        links = list(client.list_google_ads_links(parent=parent))
    except Exception as exc:
        raise _upstream_error(exc, tool=tool, action="Google Ads link lookup") from exc

    linked = sorted(str(link.customer_id) for link in links)
    data = {"propertyId": str(config["property_id"]), "customerId": customer_id, "linkedCustomerIds": linked}
    is_dry_run = request.dryRun is not False

    if customer_id in linked:
        return build_success_response(
            service=SERVICE_NAME,
            tool=tool,
            mode="dry-run" if is_dry_run else "execute",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"No change: GA4 is already linked to Google Ads {customer_id}.",
            rule_checks=rule_checks,
            changes=[],
            data=data,
            requires_confirmation=False,
            executed=False,
        )

    changes = [
        build_change(
            field="google_ads_link",
            label="Google Ads link",
            before=linked or None,
            after=customer_id,
            status="proposed",
            resource_type="google_ads_link",
        )
    ]

    if is_dry_run:
        return build_success_response(
            service=SERVICE_NAME,
            tool=tool,
            mode="dry-run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would link GA4 property {config['property_id']} to Google Ads {customer_id}.",
            rule_checks=rule_checks,
            changes=changes,
            data=data,
            requires_confirmation=True,
            executed=False,
        )

    _require_approval(request, tool, rule_checks)

    try:
        created = client.create_google_ads_link(
            parent=parent,
            google_ads_link=GoogleAdsLink(customer_id=customer_id),
        )
    except Exception as exc:
        raise _upstream_error(exc, tool=tool, action="Google Ads link creation") from exc

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool=tool,
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Linked GA4 property {config['property_id']} to Google Ads {customer_id}.",
        rule_checks=rule_checks,
        changes=changes,
        data={**data, "linkResourceName": created.name},
        requires_confirmation=False,
        executed=True,
    )
