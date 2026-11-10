"""Meta Ads write tools: dry run first, execute with a bound approvalId.

A call without approvalId returns a dry-run preview plus an approvalId that is
valid for 15 minutes and only for the exact same arguments. Calling again with
that approvalId executes. Created objects default to PAUSED.
"""

from __future__ import annotations

from typing import Any, Callable

from shared.approval_store import consume_approval, issue_approval, verify_approval
from shared.errors import AdsMcpError
from shared.meta_ads_client import (
    configured_account_id,
    create_account_object,
    get_account_snapshot,
    get_adset_schedule,
    get_adset_targeting,
    get_object_snapshot,
    update_object,
)
from shared.models import ToolRequest
from shared.responses import build_change, build_rule_check, build_success_response
from shared.runtime_config import load_meta_ads_config

SERVICE_NAME = "meta-ads"

VALID_STATUSES = {"ACTIVE", "PAUSED", "ARCHIVED"}
CREATE_STATUSES = {"ACTIVE", "PAUSED"}
VALID_OBJECTIVES = {
    "OUTCOME_AWARENESS", "OUTCOME_TRAFFIC", "OUTCOME_ENGAGEMENT",
    "OUTCOME_LEADS", "OUTCOME_APP_PROMOTION", "OUTCOME_SALES",
}
VALID_SPECIAL_AD_CATEGORIES = {
    "HOUSING", "EMPLOYMENT", "CREDIT", "ISSUES_ELECTIONS_POLITICS", "FINANCIAL_PRODUCTS_SERVICES",
}
MESSENGER_DOC_LINK = "https://fb.com/messenger_doc/"


# ── validation helpers ───────────────────────────────────────────────────────


def _invalid(message: str, tool: str) -> AdsMcpError:
    return AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message=message, tool=tool)


def _required(payload: dict[str, Any], key: str, tool: str) -> Any:
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _invalid(f"{key} is required.", tool)
    return value.strip() if isinstance(value, str) else value


def _usd_to_cents(value: Any, field: str, tool: str) -> int:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise _invalid(f"{field} must be a numeric USD amount.", tool) from exc
    if amount <= 0:
        raise _invalid(f"{field} must be greater than zero.", tool)
    return int(round(amount * 100))


def _budget(payload: dict[str, Any], tool: str, *, required: bool) -> tuple[str, int] | None:
    """Return ("daily_budget"|"lifetime_budget", cents) from the USD fields."""
    daily, lifetime = payload.get("dailyBudgetUsd"), payload.get("lifetimeBudgetUsd")
    if daily is not None and lifetime is not None:
        raise _invalid("Provide dailyBudgetUsd or lifetimeBudgetUsd, not both.", tool)
    if daily is not None:
        return "daily_budget", _usd_to_cents(daily, "dailyBudgetUsd", tool)
    if lifetime is not None:
        return "lifetime_budget", _usd_to_cents(lifetime, "lifetimeBudgetUsd", tool)
    if required:
        raise _invalid("dailyBudgetUsd or lifetimeBudgetUsd is required.", tool)
    return None


def _create_status(payload: dict[str, Any], tool: str) -> str:
    status = str(payload.get("status") or "PAUSED").upper()
    if status not in CREATE_STATUSES:
        raise _invalid("status must be 'PAUSED' (default) or 'ACTIVE'.", tool)
    return status


def _cents_to_usd(value: Any) -> float | None:
    try:
        cents = int(value)
    except (TypeError, ValueError):
        return None
    return round(cents / 100, 2) if cents else None


# ── rule checks ──────────────────────────────────────────────────────────────


def _ownership_check(config: dict[str, Any], snapshot: dict[str, Any], tool: str) -> dict[str, Any]:
    expected = configured_account_id(config, tool=tool).removeprefix("act_")
    actual = str(snapshot.get("account_id") or "")
    if actual == expected:
        return build_rule_check(rule="account-ownership", passed=True, message=f"{snapshot['objectType']} {snapshot['id']} belongs to act_{expected}.")
    return build_rule_check(
        rule="account-ownership", passed=False, severity="blocking",
        message=f"{snapshot['objectType']} {snapshot.get('id')} belongs to act_{actual or '?'}, not the configured act_{expected}.",
    )


def _currency_check(config: dict[str, Any], tool: str) -> dict[str, Any]:
    currency = get_account_snapshot(config, tool=tool).get("currency")
    if currency == "USD":
        return build_rule_check(rule="currency-usd", passed=True, message="Account currency is USD; budgets are sent in cents.")
    return build_rule_check(
        rule="currency-usd", passed=False, severity="blocking",
        message=f"Account currency is {currency}; USD budget conversion would be wrong.",
    )


def _created_paused_check(status: str) -> dict[str, Any]:
    if status == "PAUSED":
        return build_rule_check(rule="created-paused", passed=True, message="Object will be created PAUSED.")
    return build_rule_check(rule="created-paused", passed=True, severity="warning", message="Object will be created ACTIVE and may start spending once its parents are active.")


# ── shared dry-run / execute flow ────────────────────────────────────────────


def _write_flow(
    request: ToolRequest, request_id: str | None, *, tool: str,
    rule_checks: list[dict[str, Any]], changes: list[dict[str, Any]],
    preview: dict[str, Any], dry_summary: str, done_summary: str,
    execute: Callable[[], dict[str, Any]],
    bound_args: dict[str, Any] | None = None,
) -> dict:
    # bound_args lets a tool bind the approval to more than its raw arguments
    # (e.g. the SHA-256 of a local file), so a changed file fails verification.
    approval_args = request.payload if bound_args is None else bound_args
    blocked = any(not check["passed"] for check in rule_checks)

    if not request.approvalId:
        return build_success_response(
            service=SERVICE_NAME, tool=tool, mode="dry-run", business_key=request.businessKey,
            request_id=request_id,
            summary=dry_summary if not blocked else f"Blocked by business rules: {dry_summary}",
            rule_checks=rule_checks, changes=changes, data=preview,
            requires_confirmation=not blocked, executed=False,
            approval=None if blocked else issue_approval(
                service=SERVICE_NAME, tool=tool, business_key=request.businessKey, args=approval_args,
            ),
        )

    verify_approval(
        approval_id=request.approvalId, service=SERVICE_NAME, tool=tool,
        business_key=request.businessKey, args=approval_args,
    )
    if blocked:
        raise AdsMcpError(
            status_code=400, error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.", rule_checks=rule_checks, tool=tool,
        )
    # Burn the approval before calling Meta so a retry can never double-apply.
    consume_approval(request.approvalId)
    result = execute()
    for change in changes:
        change["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="execute", business_key=request.businessKey,
        request_id=request_id, summary=done_summary, rule_checks=rule_checks,
        changes=changes, data={**preview, "result": result}, executed=True,
    )


# ── tools ────────────────────────────────────────────────────────────────────


def update_status(request: ToolRequest, request_id: str | None) -> dict:
    tool = "update_status"
    payload = request.payload
    object_id = str(_required(payload, "objectId", tool))
    status = str(_required(payload, "status", tool)).upper()
    if status not in VALID_STATUSES:
        raise _invalid("status must be ACTIVE, PAUSED or ARCHIVED.", tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    snapshot = get_object_snapshot(config, object_id=object_id, tool=tool)
    label = f"{snapshot['objectType']} '{snapshot.get('name')}' ({object_id})"

    rule_checks = [_ownership_check(config, snapshot, tool)]
    if snapshot.get("status") in {"ARCHIVED", "DELETED"}:
        rule_checks.append(build_rule_check(rule="not-archived", passed=False, severity="blocking", message=f"{label} is {snapshot['status']} and cannot change status."))
    if snapshot.get("status") == status:
        rule_checks.append(build_rule_check(rule="status-differs", passed=True, severity="warning", message=f"{label} is already {status}."))

    changes = [build_change(
        field=f"{snapshot['objectType']}.status", label="Status",
        before={"status": snapshot.get("status"), "effectiveStatus": snapshot.get("effective_status")},
        after=status, resource_type=snapshot["objectType"], resource_id=object_id,
    )]
    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"object": snapshot, "graphRequest": {"method": "POST", "path": object_id, "fields": {"status": status}}},
        dry_summary=f"Would set {label} status from {snapshot.get('status')} to {status}.",
        done_summary=f"Set {label} status to {status}.",
        execute=lambda: update_object(config, object_id=object_id, fields={"status": status}, tool=tool),
    )


def update_budget(request: ToolRequest, request_id: str | None) -> dict:
    tool = "update_budget"
    payload = request.payload
    object_id = str(_required(payload, "objectId", tool))
    field, cents = _budget(payload, tool, required=True)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    snapshot = get_object_snapshot(config, object_id=object_id, tool=tool)
    label = f"{snapshot['objectType']} '{snapshot.get('name')}' ({object_id})"
    if snapshot["objectType"] == "ad":
        raise _invalid("Budgets live on campaigns (CBO) or ad sets, not ads.", tool)

    rule_checks = [_ownership_check(config, snapshot, tool), _currency_check(config, tool)]
    current_daily, current_lifetime = _cents_to_usd(snapshot.get("daily_budget")), _cents_to_usd(snapshot.get("lifetime_budget"))
    current_field = "daily_budget" if current_daily else "lifetime_budget" if current_lifetime else None
    if current_field is None:
        where = "its ad sets" if snapshot["objectType"] == "campaign" else "the campaign (CBO)"
        rule_checks.append(build_rule_check(rule="budget-level", passed=False, severity="blocking", message=f"{label} has no budget of its own; budget is set on {where}."))
    elif current_field != field:
        rule_checks.append(build_rule_check(rule="budget-type", passed=False, severity="blocking", message=f"{label} uses a {current_field.replace('_', ' ')}; Meta does not allow switching it to a {field.replace('_', ' ')}."))
    else:
        rule_checks.append(build_rule_check(rule="budget-type", passed=True, message=f"Keeps the existing {field.replace('_', ' ')}."))

    new_usd = round(cents / 100, 2)
    changes = [build_change(
        field=f"{snapshot['objectType']}.{field}", label=field.replace("_", " ").capitalize() + " (USD)",
        before=current_daily if field == "daily_budget" else current_lifetime, after=new_usd,
        resource_type=snapshot["objectType"], resource_id=object_id,
    )]
    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"object": snapshot, "graphRequest": {"method": "POST", "path": object_id, "fields": {field: cents}}},
        dry_summary=f"Would set {label} {field.replace('_', ' ')} to ${new_usd:.2f} ({cents} cents).",
        done_summary=f"Set {label} {field.replace('_', ' ')} to ${new_usd:.2f}.",
        execute=lambda: update_object(config, object_id=object_id, fields={field: cents}, tool=tool),
    )


SCHEDULE_TIMEZONES = {"USER", "ADVERTISER"}
DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _normalize_schedule(blocks: Any, tool: str) -> list[dict[str, Any]]:
    """Validate adset_schedule blocks: weekdays 0-6 (0=Sunday), whole hours, start < end."""
    if not isinstance(blocks, list) or not blocks:
        raise _invalid("schedule must be a non-empty list of {days, start_minute, end_minute} blocks.", tool)
    normalized = []
    for block in blocks:
        try:
            days = sorted({int(d) for d in block["days"]})
            start, end = int(block["start_minute"]), int(block["end_minute"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _invalid("Each schedule block needs days, start_minute and end_minute.", tool) from exc
        timezone_type = str(block.get("timezone_type") or "USER").upper()
        if not days or any(d not in range(7) for d in days):
            raise _invalid("days must be weekdays 0-6 (0 = Sunday).", tool)
        if start % 60 or end % 60 or not 0 <= start < end <= 1440:
            raise _invalid("start_minute/end_minute must be whole hours with 0 <= start < end <= 1440.", tool)
        if timezone_type not in SCHEDULE_TIMEZONES:
            raise _invalid("timezone_type must be USER or ADVERTISER.", tool)
        normalized.append({"days": days, "start_minute": start, "end_minute": end, "timezone_type": timezone_type})
    return normalized


def _describe_schedule(blocks: list[dict[str, Any]]) -> str:
    def clock(minute: int) -> str:
        hour = minute // 60
        return f"{(hour % 12) or 12}{'am' if hour < 12 or hour == 24 else 'pm'}"
    return "; ".join(
        f"{','.join(DAY_NAMES[d] for d in b['days'])} {clock(b['start_minute'])}-{clock(b['end_minute'])}"
        f" ({'advertiser' if b['timezone_type'] == 'ADVERTISER' else 'viewer'} time)"
        for b in blocks
    )


def update_adset_schedule(request: ToolRequest, request_id: str | None) -> dict:
    tool = "update_adset_schedule"
    payload = request.payload
    adset_id = str(_required(payload, "adsetId", tool))
    clone_from = str(payload.get("cloneScheduleFromAdsetId") or "").strip()
    if not clone_from and not payload.get("schedule"):
        raise _invalid("Provide schedule or cloneScheduleFromAdsetId.", tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    expected = configured_account_id(config, tool=tool).removeprefix("act_")
    target = get_adset_schedule(config, adset_id=adset_id, tool=tool)
    source = None
    if clone_from:
        source = get_adset_schedule(config, adset_id=clone_from, tool=tool)
        if not source.get("adset_schedule"):
            raise _invalid(f"Ad set {clone_from} has no schedule to clone.", tool)
        schedule = _normalize_schedule(source["adset_schedule"], tool)
    else:
        schedule = _normalize_schedule(payload["schedule"], tool)

    rule_checks = []
    for role, obj in (("account-ownership", target), ("schedule-source-ownership", source)):
        if obj is None:
            continue
        owned = str(obj.get("account_id") or "") == expected
        rule_checks.append(build_rule_check(
            rule=role, passed=owned, severity="info" if owned else "blocking",
            message=f"adset {obj.get('id')} {'belongs' if owned else 'does not belong'} to act_{expected}.",
        ))

    fields = {"adset_schedule": schedule, "pacing_type": ["day_parting"]}
    label = f"adset '{target.get('name')}' ({adset_id})"
    changes = [build_change(
        field="adset.adset_schedule", label="Ad schedule",
        before={"adset_schedule": target.get("adset_schedule"), "pacing_type": target.get("pacing_type")},
        after=fields, resource_type="adset", resource_id=adset_id,
    )]
    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={
            "adset": target,
            **({"scheduleSource": {"adsetId": clone_from, "name": source.get("name")}} if source else {}),
            "graphRequest": {"method": "POST", "path": adset_id, "fields": fields},
        },
        dry_summary=f"Would set {label} to run only {_describe_schedule(schedule)}.",
        done_summary=f"Set {label} schedule: {_describe_schedule(schedule)}.",
        execute=lambda: update_object(config, object_id=adset_id, fields=fields, tool=tool),
    )


def create_campaign(request: ToolRequest, request_id: str | None) -> dict:
    tool = "create_campaign"
    payload = request.payload
    name = _required(payload, "name", tool)
    objective = str(_required(payload, "objective", tool)).upper()
    if objective not in VALID_OBJECTIVES:
        raise _invalid(f"objective must be one of {', '.join(sorted(VALID_OBJECTIVES))}.", tool)
    categories = [str(c).upper() for c in (payload.get("specialAdCategories") or [])]
    if bad := [c for c in categories if c not in VALID_SPECIAL_AD_CATEGORIES]:
        raise _invalid(f"Unknown special ad categories: {', '.join(bad)}.", tool)
    status = _create_status(payload, tool)
    budget = _budget(payload, tool, required=False)

    fields: dict[str, Any] = {
        "name": name, "objective": objective, "status": status,
        "special_ad_categories": categories, "buying_type": "AUCTION",
    }
    if budget:
        fields[budget[0]] = budget[1]
        fields["bid_strategy"] = str(payload.get("bidStrategy") or "LOWEST_COST_WITHOUT_CAP").upper()
    else:
        # Ad set budgets: Meta requires this flag explicitly when there is no campaign budget.
        fields["is_adset_budget_sharing_enabled"] = False

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    account_id = configured_account_id(config, tool=tool)
    rule_checks = [_created_paused_check(status)]
    if budget:
        rule_checks.append(_currency_check(config, tool))

    changes = [build_change(field="campaign", label="New campaign", before=None, after=fields, resource_type="campaign")]
    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"graphRequest": {"method": "POST", "path": f"{account_id}/campaigns", "fields": fields}},
        dry_summary=f"Would create {status} campaign '{name}' ({objective}) in {account_id}" + (f" with CBO {budget[0].replace('_', ' ')} ${budget[1] / 100:.2f}." if budget else " with ad set budgets."),
        done_summary=f"Created {status} campaign '{name}'.",
        execute=lambda: create_account_object(config, edge="campaigns", fields=fields, tool=tool),
    )


def create_adset(request: ToolRequest, request_id: str | None) -> dict:
    tool = "create_adset"
    payload = request.payload
    campaign_id = str(_required(payload, "campaignId", tool))
    name = _required(payload, "name", tool)
    optimization_goal = str(_required(payload, "optimizationGoal", tool)).upper()
    status = _create_status(payload, tool)
    budget = _budget(payload, tool, required=False)
    if budget and budget[0] == "lifetime_budget" and not payload.get("endTime"):
        raise _invalid("endTime is required with lifetimeBudgetUsd.", tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    page_id = str(payload.get("pageId") or config.get("page_id"))
    clone_from = str(payload.get("cloneTargetingFromAdsetId") or "").strip()
    source: dict[str, Any] | None = None
    if clone_from:
        # Copy the source ad set's targeting verbatim (geo, ages, locales,
        # placements, Advantage+ settings); geo/age arguments are ignored.
        source = get_adset_targeting(config, adset_id=clone_from, tool=tool)
        if not isinstance(source.get("targeting"), dict) or not source["targeting"]:
            raise _invalid(f"Ad set {clone_from} has no targeting to clone.", tool)
        targeting = source["targeting"]
        targeting_summary = f"targeting cloned from ad set '{source.get('name')}' ({clone_from})"
    else:
        try:
            latitude, longitude = float(_required(payload, "latitude", tool)), float(_required(payload, "longitude", tool))
            radius = float(_required(payload, "radiusMiles", tool))
            age_min, age_max = int(payload.get("ageMin") or 18), int(payload.get("ageMax") or 65)
        except (TypeError, ValueError) as exc:
            raise _invalid("latitude, longitude, radiusMiles, ageMin and ageMax must be numbers.", tool) from exc
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise _invalid("latitude/longitude are out of range.", tool)
        if not 1 <= radius <= 50:
            raise _invalid("radiusMiles must be between 1 and 50.", tool)
        if not 18 <= age_min <= age_max <= 65:
            raise _invalid("Ages must satisfy 18 <= ageMin <= ageMax <= 65.", tool)
        targeting = {
            "geo_locations": {
                "custom_locations": [{"latitude": latitude, "longitude": longitude, "radius": radius, "distance_unit": "mile"}],
            },
            "age_min": age_min,
            "age_max": age_max,
            "targeting_automation": {"advantage_audience": int(payload.get("advantageAudience") or 0)},
        }
        targeting_summary = f"{radius:g} mi around ({latitude}, {longitude}), ages {age_min}-{age_max}"
    fields: dict[str, Any] = {
        "name": name, "campaign_id": campaign_id, "status": status,
        "optimization_goal": optimization_goal,
        "billing_event": str(payload.get("billingEvent") or "IMPRESSIONS").upper(),
        "promoted_object": {"page_id": page_id},
        "targeting": targeting,
        "destination_type": str(payload["destinationType"]).upper() if payload.get("destinationType") else None,
        "start_time": payload.get("startTime"),
        "end_time": payload.get("endTime"),
    }
    if budget:
        fields[budget[0]] = budget[1]
        fields["bid_strategy"] = str(payload.get("bidStrategy") or "LOWEST_COST_WITHOUT_CAP").upper()
    fields = {k: v for k, v in fields.items() if v is not None}

    campaign = get_object_snapshot(config, object_id=campaign_id, tool=tool)
    if campaign["objectType"] != "campaign":
        raise _invalid(f"{campaign_id} is a {campaign['objectType']}, not a campaign.", tool)
    account_id = configured_account_id(config, tool=tool)
    rule_checks = [_ownership_check(config, campaign, tool), _created_paused_check(status)]
    if source is not None:
        expected = account_id.removeprefix("act_")
        owned = str(source.get("account_id") or "") == expected
        rule_checks.append(build_rule_check(
            rule="targeting-source-ownership", passed=owned, severity="info" if owned else "blocking",
            message=(f"Targeting source ad set {clone_from} belongs to act_{expected}." if owned
                     else f"Targeting source ad set {clone_from} belongs to act_{source.get('account_id') or '?'}, not act_{expected}."),
        ))
    has_cbo = bool(_cents_to_usd(campaign.get("daily_budget")) or _cents_to_usd(campaign.get("lifetime_budget")))
    if has_cbo and budget:
        rule_checks.append(build_rule_check(rule="budget-level", passed=False, severity="blocking", message=f"Campaign '{campaign.get('name')}' uses a campaign budget (CBO); omit the ad set budget."))
    elif not has_cbo and not budget:
        rule_checks.append(build_rule_check(rule="budget-level", passed=False, severity="blocking", message=f"Campaign '{campaign.get('name')}' has no campaign budget; the ad set needs dailyBudgetUsd or lifetimeBudgetUsd."))
    else:
        rule_checks.append(build_rule_check(rule="budget-level", passed=True, message="Budget is set at the correct level."))
    if budget:
        rule_checks.append(_currency_check(config, tool))

    changes = [build_change(field="adset", label="New ad set", before=None, after=fields, resource_type="adset")]
    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={
            "campaign": campaign,
            **({"targetingSource": {"adsetId": clone_from, "name": source.get("name")}} if source is not None else {}),
            "graphRequest": {"method": "POST", "path": f"{account_id}/adsets", "fields": fields},
        },
        dry_summary=(
            f"Would create {status} ad set '{name}' in campaign '{campaign.get('name')}': {optimization_goal}"
            f"{' → ' + fields['destination_type'] if 'destination_type' in fields else ''}, "
            f"{targeting_summary}"
            + (f", {budget[0].replace('_', ' ')} ${budget[1] / 100:.2f}." if budget else ".")
        ),
        done_summary=f"Created {status} ad set '{name}'.",
        execute=lambda: create_account_object(config, edge="adsets", fields=fields, tool=tool),
    )


def _creative_spec(payload: dict[str, Any], config: dict[str, Any], tool: str) -> dict[str, Any]:
    if payload.get("objectStoryId"):
        return {"object_story_id": str(payload["objectStoryId"])}

    page_id = str(payload.get("pageId") or config.get("page_id"))
    message = _required(payload, "message", tool)
    cta_type = str(payload.get("callToActionType") or "MESSAGE_PAGE").upper()
    cta: dict[str, Any] = {"type": cta_type}
    link = payload.get("link")
    if cta_type == "MESSAGE_PAGE":
        cta["value"] = {"app_destination": str(payload.get("appDestination") or "MESSENGER").upper()}
        link = link or MESSENGER_DOC_LINK
    elif link:
        cta["value"] = {"link": link}

    image_hash, image_url, video_id = payload.get("imageHash"), payload.get("imageUrl"), payload.get("videoId")
    if video_id:
        if not (image_hash or image_url):
            raise _invalid("A video ad needs a thumbnail: imageHash or imageUrl.", tool)
        video_data = {
            "video_id": str(video_id), "message": message, "call_to_action": cta,
            "title": payload.get("headline"),
            "image_hash": image_hash, "image_url": None if image_hash else image_url,
        }
        return {"object_story_spec": {"page_id": page_id, "video_data": {k: v for k, v in video_data.items() if v is not None}}}

    if not (image_hash or image_url):
        raise _invalid("Provide objectStoryId, or message plus imageHash, imageUrl or videoId.", tool)
    if not link:
        raise _invalid(f"link is required for call_to_action_type {cta_type}.", tool)
    link_data = {
        "message": message, "link": link, "call_to_action": cta, "name": payload.get("headline"),
        "image_hash": image_hash, "picture": None if image_hash else image_url,
    }
    return {"object_story_spec": {"page_id": page_id, "link_data": {k: v for k, v in link_data.items() if v is not None}}}


def create_ad(request: ToolRequest, request_id: str | None) -> dict:
    tool = "create_ad"
    payload = request.payload
    adset_id = str(_required(payload, "adsetId", tool))
    name = _required(payload, "name", tool)
    status = _create_status(payload, tool)

    config = load_meta_ads_config(business_key=request.businessKey, tool=tool)
    creative_fields = {"name": f"{name} creative", **_creative_spec(payload, config, tool)}
    adset = get_object_snapshot(config, object_id=adset_id, tool=tool)
    if adset["objectType"] != "adset":
        raise _invalid(f"{adset_id} is a {adset['objectType']}, not an ad set.", tool)
    account_id = configured_account_id(config, tool=tool)
    ad_fields = {"name": name, "adset_id": adset_id, "status": status, "creative": {"creative_id": "<from step 1>"}}
    rule_checks = [_ownership_check(config, adset, tool), _created_paused_check(status)]

    def execute() -> dict[str, Any]:
        creative = create_account_object(config, edge="adcreatives", fields=creative_fields, tool=tool)
        creative_id = str(creative.get("id"))
        try:
            ad = create_account_object(config, edge="ads", fields={**ad_fields, "creative": {"creative_id": creative_id}}, tool=tool)
        except AdsMcpError as exc:
            exc.details = {**exc.details, "creativeId": creative_id, "note": "Ad creative was created but the ad was not."}
            raise
        return {"creativeId": creative_id, "adId": ad.get("id")}

    changes = [
        build_change(field="adcreative", label="New ad creative", before=None, after=creative_fields, resource_type="adcreative"),
        build_change(field="ad", label="New ad", before=None, after=ad_fields, resource_type="ad"),
    ]
    return _write_flow(
        request, request_id, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={
            "adset": adset,
            "graphRequests": [
                {"step": 1, "method": "POST", "path": f"{account_id}/adcreatives", "fields": creative_fields},
                {"step": 2, "method": "POST", "path": f"{account_id}/ads", "fields": ad_fields},
            ],
        },
        dry_summary=f"Would create an ad creative, then {status} ad '{name}' in ad set '{adset.get('name')}'.",
        done_summary=f"Created {status} ad '{name}'.",
        execute=execute,
    )
