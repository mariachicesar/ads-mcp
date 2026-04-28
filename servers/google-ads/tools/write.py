from shared.errors import AdsMcpError
from shared.google_ads_client import (
    get_campaign_budget_snapshot,
    update_campaign_budget_amount,
    get_campaign_status_snapshot,
    mutate_campaign_status,
    get_ad_group_status_snapshot,
    mutate_ad_group_status,
    get_keyword_snapshot,
    mutate_keyword_bid,
    get_ad_snapshot,
    mutate_ad_status,
    add_negative_keyword_to_campaign,
    create_responsive_search_ad,
    mutate_campaign_bidding_strategy,
)
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


_VALID_STATUSES = {"ENABLED", "PAUSED"}


def set_campaign_status(request, request_id: str | None) -> dict:
    campaign_name = request.payload.get("campaignName")
    new_status = (request.payload.get("status") or "").upper()

    if not campaign_name:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="campaignName is required.",
            tool="set_campaign_status",
        )
    if new_status not in _VALID_STATUSES:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="status must be 'ENABLED' or 'PAUSED'.",
            tool="set_campaign_status",
        )

    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        payload=request.payload,
        campaign_name=campaign_name,
        ad_group_name=None,
        change_type="status",
    )

    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="set_campaign_status",
    )
    snapshot = get_campaign_status_snapshot(
        config,
        campaign_name=campaign_name,
        tool="set_campaign_status",
    )

    changes = [
        build_change(
            field="campaign.status",
            label="Campaign status",
            before=snapshot["currentStatus"],
            after=new_status,
            status="proposed" if request.dryRun is not False else "applied",
        )
    ]

    if request.dryRun is not False:
        return build_success_response(
            service=SERVICE_NAME,
            tool="set_campaign_status",
            mode="dry-run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would set {campaign_name} status to {new_status}.",
            rule_checks=rule_checks,
            changes=changes,
            data={
                "campaignId": snapshot["campaignId"],
                "campaignResourceName": snapshot["campaignResourceName"],
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
            tool="set_campaign_status",
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool="set_campaign_status",
        )

    mutation_result = mutate_campaign_status(
        config,
        campaign_resource_name=snapshot["campaignResourceName"],
        new_status=new_status,
        tool="set_campaign_status",
    )

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool="set_campaign_status",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Set {campaign_name} status to {new_status}.",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )


def set_ad_group_status(request, request_id: str | None) -> dict:
    ad_group_name = request.payload.get("adGroupName")
    campaign_name = request.payload.get("campaignName")
    new_status = (request.payload.get("status") or "").upper()

    if not ad_group_name:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="adGroupName is required.",
            tool="set_ad_group_status",
        )
    if new_status not in _VALID_STATUSES:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="status must be 'ENABLED' or 'PAUSED'.",
            tool="set_ad_group_status",
        )

    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        payload=request.payload,
        campaign_name=campaign_name,
        ad_group_name=ad_group_name,
        change_type="status",
    )

    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="set_ad_group_status",
    )
    snapshot = get_ad_group_status_snapshot(
        config,
        ad_group_name=ad_group_name,
        campaign_name=campaign_name,
        tool="set_ad_group_status",
    )

    changes = [
        build_change(
            field="ad_group.status",
            label="Ad group status",
            before=snapshot["currentStatus"],
            after=new_status,
            status="proposed" if request.dryRun is not False else "applied",
        )
    ]

    if request.dryRun is not False:
        return build_success_response(
            service=SERVICE_NAME,
            tool="set_ad_group_status",
            mode="dry-run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would set ad group '{ad_group_name}' status to {new_status}.",
            rule_checks=rule_checks,
            changes=changes,
            data={
                "adGroupId": snapshot["adGroupId"],
                "adGroupResourceName": snapshot["adGroupResourceName"],
                "campaignName": snapshot["campaignName"],
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
            tool="set_ad_group_status",
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool="set_ad_group_status",
        )

    mutation_result = mutate_ad_group_status(
        config,
        ad_group_resource_name=snapshot["adGroupResourceName"],
        new_status=new_status,
        tool="set_ad_group_status",
    )

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool="set_ad_group_status",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Set ad group '{ad_group_name}' status to {new_status}.",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )


_VALID_STATUSES = {"ENABLED", "PAUSED"}

_VALID_MATCH_TYPES = {"BROAD", "PHRASE", "EXACT"}

_VALID_BIDDING_STRATEGIES = {
    "MAXIMIZE_CLICKS",
    "MAXIMIZE_CONVERSIONS",
    "TARGET_CPA",
    "TARGET_ROAS",
    "MANUAL_CPC",
}


def add_negative_keyword(request, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="add_negative_keyword",
    )
    payload = request.payload or {}

    campaign_name = payload.get("campaignName")
    keyword_text = payload.get("keywordText")
    match_type = (payload.get("matchType") or "EXACT").upper()

    if not campaign_name:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="campaignName is required.",
            tool="add_negative_keyword",
        )
    if not keyword_text:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="keywordText is required.",
            tool="add_negative_keyword",
        )
    if match_type not in _VALID_MATCH_TYPES:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"matchType must be one of: {', '.join(sorted(_VALID_MATCH_TYPES))}.",
            tool="add_negative_keyword",
        )

    rule_context = {
        "campaignName": campaign_name,
        "tool": "add_negative_keyword",
        "operation": "ADD_NEGATIVE_KEYWORD",
        **(request.ruleContext or {}),
    }
    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        context=rule_context,
    )

    changes = [
        build_change(
            field="negativeCampaignKeyword",
            before=None,
            after={"keywordText": keyword_text, "matchType": match_type},
            resource_type="campaign_criterion",
            resource_id=campaign_name,
        )
    ]

    if request.dryRun:
        return build_success_response(
            service=SERVICE_NAME,
            tool="add_negative_keyword",
            mode="dry_run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would add negative keyword '{keyword_text}' [{match_type}] to campaign '{campaign_name}'.",
            rule_checks=rule_checks,
            changes=changes,
            data={"campaignName": campaign_name, "keywordText": keyword_text, "matchType": match_type},
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
            tool="add_negative_keyword",
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool="add_negative_keyword",
        )

    mutation_result = add_negative_keyword_to_campaign(
        config,
        campaign_name=campaign_name,
        keyword_text=keyword_text,
        match_type=match_type,
        tool="add_negative_keyword",
    )

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool="add_negative_keyword",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Added negative keyword '{keyword_text}' [{match_type}] to campaign '{campaign_name}'.",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )


def update_keyword_bid(request, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="update_keyword_bid",
    )
    payload = request.payload or {}

    keyword_text = payload.get("keywordText")
    campaign_name = payload.get("campaignName")
    ad_group_name = payload.get("adGroupName")
    new_cpc_bid = payload.get("newCpcBid")

    if not keyword_text:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="keywordText is required.",
            tool="update_keyword_bid",
        )
    if new_cpc_bid is None:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="newCpcBid is required.",
            tool="update_keyword_bid",
        )

    try:
        new_cpc_bid_float = float(new_cpc_bid)
    except (TypeError, ValueError) as exc:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="newCpcBid must be a numeric value.",
            tool="update_keyword_bid",
        ) from exc

    if new_cpc_bid_float <= 0:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="newCpcBid must be greater than zero.",
            tool="update_keyword_bid",
        )

    new_cpc_bid_micros = round(new_cpc_bid_float * 1_000_000)

    rule_context = {
        "campaignName": campaign_name,
        "adGroupName": ad_group_name,
        "tool": "update_keyword_bid",
        "operation": "UPDATE_KEYWORD_BID",
        **(request.ruleContext or {}),
    }
    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        context=rule_context,
    )

    snapshot = get_keyword_snapshot(
        config,
        keyword_text=keyword_text,
        campaign_name=campaign_name,
        ad_group_name=ad_group_name,
        tool="update_keyword_bid",
    )

    changes = [
        build_change(
            field="cpcBidMicros",
            before=snapshot["currentCpcBidMicros"],
            after=new_cpc_bid_micros,
            resource_type="ad_group_criterion",
            resource_id=snapshot["criterionId"],
        )
    ]

    if request.dryRun:
        return build_success_response(
            service=SERVICE_NAME,
            tool="update_keyword_bid",
            mode="dry_run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would update bid for '{keyword_text}' from ${snapshot['currentCpcBid']:.2f} to ${new_cpc_bid_float:.2f}.",
            rule_checks=rule_checks,
            changes=changes,
            data=snapshot,
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
            tool="update_keyword_bid",
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool="update_keyword_bid",
        )

    mutation_result = mutate_keyword_bid(
        config,
        keyword_resource_name=snapshot["resourceName"],
        new_cpc_bid_micros=new_cpc_bid_micros,
        tool="update_keyword_bid",
    )

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool="update_keyword_bid",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Updated bid for '{keyword_text}' to ${new_cpc_bid_float:.2f}.",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )


def update_ad_status(request, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="update_ad_status",
    )
    payload = request.payload or {}

    ad_id = payload.get("adId")
    new_status = (payload.get("status") or "").upper()

    if not ad_id:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="adId is required.",
            tool="update_ad_status",
        )
    if new_status not in _VALID_STATUSES:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}.",
            tool="update_ad_status",
        )

    snapshot = get_ad_snapshot(config, ad_id=str(ad_id), tool="update_ad_status")

    rule_context = {
        "campaignName": snapshot["campaignName"],
        "adGroupName": snapshot["adGroupName"],
        "tool": "update_ad_status",
        "operation": "UPDATE_AD_STATUS",
        **(request.ruleContext or {}),
    }
    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        context=rule_context,
    )

    changes = [
        build_change(
            field="adStatus",
            before=snapshot["currentStatus"],
            after=new_status,
            resource_type="ad_group_ad",
            resource_id=str(ad_id),
        )
    ]

    if request.dryRun:
        return build_success_response(
            service=SERVICE_NAME,
            tool="update_ad_status",
            mode="dry_run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would set ad {ad_id} status to {new_status}.",
            rule_checks=rule_checks,
            changes=changes,
            data=snapshot,
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
            tool="update_ad_status",
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool="update_ad_status",
        )

    mutation_result = mutate_ad_status(
        config,
        ad_resource_name=snapshot["adResourceName"],
        new_status=new_status,
        tool="update_ad_status",
    )

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool="update_ad_status",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Set ad {ad_id} status to {new_status}.",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )


def create_rsa(request, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="create_rsa",
    )
    payload = request.payload or {}

    campaign_name = payload.get("campaignName")
    ad_group_name = payload.get("adGroupName")
    headlines = payload.get("headlines") or []
    descriptions = payload.get("descriptions") or []
    final_url = payload.get("finalUrl")

    if not campaign_name:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message="campaignName is required.", tool="create_rsa")
    if not ad_group_name:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message="adGroupName is required.", tool="create_rsa")
    if not final_url:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message="finalUrl is required.", tool="create_rsa")
    if len(headlines) < 3:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message="At least 3 headlines are required.", tool="create_rsa")
    if len(headlines) > 15:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message="Maximum 15 headlines allowed.", tool="create_rsa")
    if len(descriptions) < 2:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message="At least 2 descriptions are required.", tool="create_rsa")
    if len(descriptions) > 4:
        raise AdsMcpError(status_code=400, error_code="REQUEST_INVALID", message="Maximum 4 descriptions allowed.", tool="create_rsa")

    rule_context = {
        "campaignName": campaign_name,
        "adGroupName": ad_group_name,
        "tool": "create_rsa",
        "operation": "CREATE_AD",
        **(request.ruleContext or {}),
    }
    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        context=rule_context,
    )

    changes = [
        build_change(
            field="responsiveSearchAd",
            before=None,
            after={
                "campaignName": campaign_name,
                "adGroupName": ad_group_name,
                "headlines": headlines,
                "descriptions": descriptions,
                "finalUrl": final_url,
            },
            resource_type="ad_group_ad",
            resource_id=f"{campaign_name}/{ad_group_name}",
        )
    ]

    if request.dryRun:
        return build_success_response(
            service=SERVICE_NAME,
            tool="create_rsa",
            mode="dry_run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would create RSA in '{ad_group_name}' ({campaign_name}) with {len(headlines)} headlines.",
            rule_checks=rule_checks,
            changes=changes,
            data={"campaignName": campaign_name, "adGroupName": ad_group_name, "headlines": headlines, "descriptions": descriptions, "finalUrl": final_url},
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
            tool="create_rsa",
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool="create_rsa",
        )

    mutation_result = create_responsive_search_ad(
        config,
        campaign_name=campaign_name,
        ad_group_name=ad_group_name,
        headlines=headlines,
        descriptions=descriptions,
        final_url=final_url,
        tool="create_rsa",
    )

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool="create_rsa",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Created RSA in '{ad_group_name}' ({campaign_name}).",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )


def update_campaign_bidding_strategy(request, request_id: str | None) -> dict:
    config = load_google_ads_sdk_config(
        business_key=request.businessKey,
        tool="update_campaign_bidding_strategy",
    )
    payload = request.payload or {}

    campaign_name = payload.get("campaignName")
    bidding_strategy = (payload.get("biddingStrategy") or "").upper()
    target_cpa_micros = payload.get("targetCpaMicros")
    target_roas = payload.get("targetRoas")

    if not campaign_name:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="campaignName is required.",
            tool="update_campaign_bidding_strategy",
        )
    if bidding_strategy not in _VALID_BIDDING_STRATEGIES:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"biddingStrategy must be one of: {', '.join(sorted(_VALID_BIDDING_STRATEGIES))}.",
            tool="update_campaign_bidding_strategy",
        )

    # fetch campaign snapshot to get resource name
    snapshot = get_campaign_status_snapshot(config, campaign_name=campaign_name, tool="update_campaign_bidding_strategy")

    rule_context = {
        "campaignName": campaign_name,
        "tool": "update_campaign_bidding_strategy",
        "operation": "UPDATE_BIDDING_STRATEGY",
        **(request.ruleContext or {}),
    }
    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        context=rule_context,
    )

    after_value: dict = {"biddingStrategy": bidding_strategy}
    if target_cpa_micros:
        after_value["targetCpaMicros"] = int(target_cpa_micros)
    if target_roas:
        after_value["targetRoas"] = float(target_roas)

    changes = [
        build_change(
            field="biddingStrategy",
            before=None,
            after=after_value,
            resource_type="campaign",
            resource_id=snapshot["campaignId"],
        )
    ]

    if request.dryRun:
        return build_success_response(
            service=SERVICE_NAME,
            tool="update_campaign_bidding_strategy",
            mode="dry_run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would update campaign '{campaign_name}' bidding to {bidding_strategy}.",
            rule_checks=rule_checks,
            changes=changes,
            data=snapshot,
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
            tool="update_campaign_bidding_strategy",
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool="update_campaign_bidding_strategy",
        )

    mutation_result = mutate_campaign_bidding_strategy(
        config,
        campaign_resource_name=snapshot["campaignResourceName"],
        bidding_strategy=bidding_strategy,
        target_cpa_micros=int(target_cpa_micros) if target_cpa_micros else None,
        target_roas=float(target_roas) if target_roas else None,
        tool="update_campaign_bidding_strategy",
    )

    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool="update_campaign_bidding_strategy",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Updated campaign '{campaign_name}' bidding strategy to {bidding_strategy}.",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )
