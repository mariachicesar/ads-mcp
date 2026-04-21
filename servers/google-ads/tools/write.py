from shared.errors import AdsMcpError
from shared.google_ads_client import get_campaign_budget_snapshot, update_campaign_budget_amount
from shared.responses import build_change, build_success_response
from shared.rules import evaluate_google_ads_mutation_rules
from shared.runtime_config import load_google_ads_sdk_config


SERVICE_NAME = "google-ads"


def _parse_budget_amount(value) -> tuple[float, int]:
    try:
        budget_amount = float(value)
    except (TypeError, ValueError) as exc:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="newDailyBudget must be a numeric value.",
            tool="update_campaign_budget",
        ) from exc

    if budget_amount <= 0:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="newDailyBudget must be greater than zero.",
            tool="update_campaign_budget",
        )

    return budget_amount, int(round(budget_amount * 1_000_000))


def update_campaign_budget(request, request_id: str | None) -> dict:
    campaign_name = request.payload.get("campaignName")
    new_budget = request.payload.get("newDailyBudget")

    if not campaign_name or new_budget is None:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="campaignName and newDailyBudget are required.",
            tool="update_campaign_budget",
        )

    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        payload=request.payload,
        campaign_name=campaign_name,
        ad_group_name=request.payload.get("adGroupName"),
        change_type="budget",
    )

    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="update_campaign_budget",
    )
    snapshot = get_campaign_budget_snapshot(
        config,
        campaign_name=campaign_name,
        tool="update_campaign_budget",
    )

    budget_amount, new_budget_micros = _parse_budget_amount(new_budget)

    changes = [
        build_change(
            field="campaign.daily_budget",
            label="Daily budget",
            before=snapshot["currentDailyBudget"],
            after=budget_amount,
            status="proposed" if request.dryRun is not False else "applied",
        )
    ]

    if request.dryRun is not False:
        return build_success_response(
            service=SERVICE_NAME,
            tool="update_campaign_budget",
            mode="dry-run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would update {campaign_name} budget to {budget_amount}",
            rule_checks=rule_checks,
            changes=changes,
            data={
                "campaignId": snapshot["campaignId"],
                "campaignStatus": snapshot["campaignStatus"],
                "budgetResourceName": snapshot["campaignBudgetResourceName"],
            },
            requires_confirmation=True,
            executed=False,
        )

    if not request.approvalId:
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="approvalId is required for execute requests.",
            retryable=False,
            rule_checks=rule_checks,
            tool="update_campaign_budget",
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool="update_campaign_budget",
        )

    mutation_result = update_campaign_budget_amount(
        config,
        budget_resource_name=snapshot["campaignBudgetResourceName"],
        new_budget_micros=new_budget_micros,
        tool="update_campaign_budget",
    )

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool="update_campaign_budget",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Updated {campaign_name} budget to {budget_amount}",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )
