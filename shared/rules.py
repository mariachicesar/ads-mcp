from __future__ import annotations

from typing import Any

from shared.responses import build_rule_check

GOOGLE_ADS_RULES: dict[str, dict[str, Any]] = {
    "rnr-electrician": {
        "protected_campaigns": {"Pasadena-San Marino"},
        "protected_ad_groups": {"NoHo EV Charge"},
        "lock_geo_changes": True,
        "keyword_changes_require_explicit_approval": True,
    },
    "gq-painting": {
        "campaign_structure_requires_explicit_approval": True,
    },
}

GEO_MUTATION_KEYS = {
    "geoTargets",
    "newGeoTargets",
    "locationTargets",
    "locations",
    "excludedLocations",
}
KEYWORD_MUTATION_KEYS = {
    "keywords",
    "newKeywords",
    "removedKeywords",
    "keywordUpdates",
}
STRUCTURE_MUTATION_KEYS = {
    "campaignStructure",
    "adGroupStructure",
    "newCampaigns",
    "removedCampaigns",
}


def _has_any_key(payload: dict[str, Any], keys: set[str]) -> bool:
    return any(key in payload for key in keys)


def evaluate_google_ads_mutation_rules(
    *,
    business_key: str,
    payload: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    campaign_name: str | None = None,
    ad_group_name: str | None = None,
    change_type: str | None = None,
) -> list[dict[str, Any]]:
    # Backward-compatible normalization: older write handlers pass `context`
    # while newer handlers pass `payload` plus explicit campaign/ad group args.
    normalized_payload = payload or context or {}

    if campaign_name is None:
        campaign_name = (
            normalized_payload.get("campaignName")
            or normalized_payload.get("campaign_name")
        )
    if ad_group_name is None:
        ad_group_name = (
            normalized_payload.get("adGroupName")
            or normalized_payload.get("ad_group_name")
        )

    rules = GOOGLE_ADS_RULES.get(business_key, {})
    rule_checks: list[dict[str, Any]] = []

    protected_campaigns = rules.get("protected_campaigns", set())
    if campaign_name and campaign_name in protected_campaigns:
        rule_checks.append(
            build_rule_check(
                rule="protected-campaign",
                passed=False,
                message=f"{campaign_name} is protected and cannot be modified.",
                severity="blocking",
            )
        )
    else:
        rule_checks.append(
            build_rule_check(
                rule="protected-campaign",
                passed=True,
                message="Campaign is not on protected list.",
            )
        )

    protected_ad_groups = rules.get("protected_ad_groups", set())
    if ad_group_name and ad_group_name in protected_ad_groups:
        rule_checks.append(
            build_rule_check(
                rule="protected-ad-group",
                passed=False,
                message=f"{ad_group_name} is protected and cannot be modified.",
                severity="blocking",
            )
        )

    if rules.get("lock_geo_changes"):
        if change_type == "geo" or _has_any_key(normalized_payload, GEO_MUTATION_KEYS):
            rule_checks.append(
                build_rule_check(
                    rule="geo-target-lock",
                    passed=False,
                    message="Geo targeting changes are locked until the USC Village move is explicitly confirmed.",
                    severity="blocking",
                )
            )

    if rules.get("keyword_changes_require_explicit_approval"):
        if change_type == "keywords" or _has_any_key(normalized_payload, KEYWORD_MUTATION_KEYS):
            rule_checks.append(
                build_rule_check(
                    rule="keyword-approval-required",
                    passed=False,
                    message="Keyword changes require explicit approval for this business.",
                    severity="blocking",
                )
            )

    if rules.get("campaign_structure_requires_explicit_approval"):
        if change_type == "campaign-structure" or _has_any_key(normalized_payload, STRUCTURE_MUTATION_KEYS):
            rule_checks.append(
                build_rule_check(
                    rule="campaign-structure-lock",
                    passed=False,
                    message="Campaign structure changes require explicit approval for this business.",
                    severity="blocking",
                )
            )

    return rule_checks
