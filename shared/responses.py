from __future__ import annotations

from typing import Any

from shared.models import Change, RuleCheck


def build_rule_check(
    *,
    rule: str,
    passed: bool,
    message: str,
    severity: str = "info",
    source: str = "CLAUDE.md",
) -> dict[str, Any]:
    return RuleCheck(
        rule=rule,
        passed=passed,
        message=message,
        severity=severity,
        source=source,
    ).model_dump()


def build_change(
    *,
    field: str,
    label: str | None = None,
    before: Any,
    after: Any,
    status: str = "proposed",
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> dict[str, Any]:
    change = Change(
        field=field,
        label=label or field,
        before=before,
        after=after,
        status=status,
    ).model_dump()

    # Optional identifiers used by some write handlers.
    if resource_type is not None:
        change["resourceType"] = resource_type
    if resource_id is not None:
        change["resourceId"] = resource_id

    return change


def build_success_response(
    *,
    service: str,
    tool: str,
    mode: str,
    business_key: str,
    request_id: str | None,
    summary: str,
    data: dict[str, Any] | None = None,
    rule_checks: list[dict[str, Any]] | None = None,
    changes: list[dict[str, Any]] | None = None,
    requires_confirmation: bool = False,
    executed: bool = False,
    freshness: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    response = {
        "ok": True,
        "service": service,
        "tool": tool,
        "mode": mode,
        "businessKey": business_key,
        "requestId": request_id,
        "summary": summary,
        "ruleChecks": rule_checks or [],
        "changes": changes or [],
        "data": data or {},
        "requiresConfirmation": requires_confirmation,
        "executed": executed,
    }
    if freshness is not None:
        response["freshness"] = freshness
    if warnings:
        response["warnings"] = warnings
    return response
