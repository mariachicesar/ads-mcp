from __future__ import annotations

import pytest

from shared import approval_store, meta_ads_client
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.service_import import load_service_module

write = load_service_module("meta-ads", "tools.write")

CONFIG = {"ad_account_id": "act_123", "access_token": "tok", "app_secret": "sec", "page_id": "999"}
CAMPAIGN = {"id": "c1", "name": "Campaign | Messages | $15", "account_id": "123", "status": "ACTIVE",
            "effective_status": "ACTIVE", "daily_budget": "1500", "objectType": "campaign"}


@pytest.fixture
def graph(monkeypatch):
    """Stub every Graph call the write tools make and record writes."""
    calls: dict[str, list] = {"update": [], "create": []}
    objects = {"c1": CAMPAIGN}

    monkeypatch.setattr(write, "load_meta_ads_config", lambda **kwargs: dict(CONFIG))
    monkeypatch.setattr(write, "get_object_snapshot", lambda config, *, object_id, tool: dict(objects[object_id]))
    monkeypatch.setattr(write, "get_account_snapshot", lambda config, *, tool: {"currency": "USD"})

    def update_object(config, *, object_id, fields, tool):
        calls["update"].append((object_id, fields))
        return {"success": True}

    def create_account_object(config, *, edge, fields, tool):
        calls["create"].append((edge, fields))
        return {"id": f"new-{edge}"}

    monkeypatch.setattr(write, "update_object", update_object)
    monkeypatch.setattr(write, "create_account_object", create_account_object)
    monkeypatch.setattr(approval_store, "_MEMORY_APPROVAL_STORE", approval_store.InMemoryApprovalStore())
    calls["objects"] = objects
    return calls


def _req(payload, approval_id=None):
    return ToolRequest(businessKey="el-cuis", approvalId=approval_id, payload=payload)


def test_update_status_dry_run_issues_approval_without_writing(graph):
    response = write.update_status(_req({"objectId": "c1", "status": "PAUSED"}), None)

    assert response["mode"] == "dry-run" and response["executed"] is False
    assert response["requiresConfirmation"] is True
    assert response["approvalId"] and response["approvalExpiresAt"]
    assert response["changes"][0]["before"]["status"] == "ACTIVE"
    assert response["changes"][0]["after"] == "PAUSED"
    assert graph["update"] == []


def test_update_status_executes_once_with_matching_approval(graph):
    payload = {"objectId": "c1", "status": "PAUSED"}
    approval_id = write.update_status(_req(payload), None)["approvalId"]

    response = write.update_status(_req(payload, approval_id), None)

    assert response["mode"] == "execute" and response["executed"] is True
    assert graph["update"] == [("c1", {"status": "PAUSED"})]
    with pytest.raises(AdsMcpError) as exc:
        write.update_status(_req(payload, approval_id), None)
    assert exc.value.error_code == "APPROVAL_INVALID"
    assert len(graph["update"]) == 1


def test_approval_is_bound_to_exact_arguments(graph):
    approval_id = write.update_status(_req({"objectId": "c1", "status": "PAUSED"}), None)["approvalId"]

    with pytest.raises(AdsMcpError) as exc:
        write.update_status(_req({"objectId": "c1", "status": "ARCHIVED"}, approval_id), None)

    assert exc.value.error_code == "APPROVAL_MISMATCH"
    assert graph["update"] == []


def test_made_up_approval_id_is_rejected(graph):
    with pytest.raises(AdsMcpError) as exc:
        write.update_status(_req({"objectId": "c1", "status": "PAUSED"}, "not-issued"), None)
    assert exc.value.error_code == "APPROVAL_INVALID"


def test_approval_expires(graph, monkeypatch):
    payload = {"objectId": "c1", "status": "PAUSED"}
    approval_id = write.update_status(_req(payload), None)["approvalId"]
    now = approval_store.time.time()
    monkeypatch.setattr(approval_store.time, "time", lambda: now + approval_store.APPROVAL_TTL_SECONDS + 1)

    with pytest.raises(AdsMcpError) as exc:
        write.update_status(_req(payload, approval_id), None)
    assert exc.value.error_code == "APPROVAL_INVALID"


def test_other_account_object_is_blocked_and_gets_no_approval(graph):
    graph["objects"]["x"] = {**CAMPAIGN, "id": "x", "account_id": "777"}

    response = write.update_status(_req({"objectId": "x", "status": "PAUSED"}), None)

    assert response["requiresConfirmation"] is False
    assert "approvalId" not in response
    assert any(c["rule"] == "account-ownership" and not c["passed"] for c in response["ruleChecks"])


def test_update_budget_converts_dollars_to_cents(graph):
    response = write.update_budget(_req({"objectId": "c1", "dailyBudgetUsd": 20.5}), None)

    assert response["data"]["graphRequest"]["fields"] == {"daily_budget": 2050}
    assert response["changes"][0]["before"] == 15.0 and response["changes"][0]["after"] == 20.5


def test_update_budget_blocks_switching_budget_type(graph):
    response = write.update_budget(_req({"objectId": "c1", "lifetimeBudgetUsd": 100}), None)

    assert "approvalId" not in response
    assert any(c["rule"] == "budget-type" and not c["passed"] for c in response["ruleChecks"])


def test_create_campaign_without_budget_disables_budget_sharing_and_is_paused(graph):
    payload = {"name": "Test", "objective": "OUTCOME_ENGAGEMENT", "specialAdCategories": []}
    response = write.create_campaign(_req(payload), None)
    fields = response["data"]["graphRequest"]["fields"]

    assert fields["status"] == "PAUSED"
    assert fields["is_adset_budget_sharing_enabled"] is False
    assert "daily_budget" not in fields

    write.create_campaign(_req(payload, response["approvalId"]), None)
    assert graph["create"] == [("campaigns", fields)]


def test_create_campaign_with_cbo_budget(graph):
    response = write.create_campaign(_req({"name": "T", "objective": "OUTCOME_LEADS", "dailyBudgetUsd": 15}), None)
    fields = response["data"]["graphRequest"]["fields"]

    assert fields["daily_budget"] == 1500
    assert fields["bid_strategy"] == "LOWEST_COST_WITHOUT_CAP"
    assert "is_adset_budget_sharing_enabled" not in fields


def test_create_adset_builds_radius_targeting(graph):
    graph["objects"]["c2"] = {**CAMPAIGN, "id": "c2", "daily_budget": None}
    response = write.create_adset(_req({
        "campaignId": "c2", "name": "LA 10mi", "optimizationGoal": "CONVERSATIONS",
        "destinationType": "MESSENGER", "latitude": 34.02, "longitude": -118.28,
        "radiusMiles": 10, "ageMin": 25, "ageMax": 55, "dailyBudgetUsd": 15,
    }), None)
    fields = response["data"]["graphRequest"]["fields"]

    assert response["approvalId"]
    assert fields["status"] == "PAUSED" and fields["daily_budget"] == 1500
    assert fields["promoted_object"] == {"page_id": "999"}
    assert fields["destination_type"] == "MESSENGER"
    assert fields["targeting"] == {
        "geo_locations": {"custom_locations": [{"latitude": 34.02, "longitude": -118.28, "radius": 10.0, "distance_unit": "mile"}]},
        "age_min": 25, "age_max": 55, "targeting_automation": {"advantage_audience": 0},
    }


SOURCE_TARGETING = {
    "age_min": 25, "age_max": 65,
    "geo_locations": {"cities": [{"key": "2420379", "radius": 30, "distance_unit": "mile"}],
                      "location_types": ["frequently_in", "home", "recent"]},
    "locales": [6, 7, 23],
    "targeting_automation": {"advantage_audience": 1, "individual_setting": {"geo": 0}},
    "publisher_platforms": ["facebook", "instagram"],
    "facebook_positions": ["feed", "story", "facebook_reels", "profile_feed"],
    "instagram_positions": ["story", "reels"],
}


@pytest.fixture
def source_adset(monkeypatch):
    sources = {"as1": {"id": "as1", "name": "Vieja Chillona Ad", "account_id": "123", "targeting": SOURCE_TARGETING}}
    monkeypatch.setattr(write, "get_adset_targeting", lambda config, *, adset_id, tool: dict(sources[adset_id]))
    return sources


def test_create_adset_clones_targeting_verbatim_from_source_adset(graph, source_adset):
    graph["objects"]["c2"] = {**CAMPAIGN, "id": "c2", "daily_budget": None}
    response = write.create_adset(_req({
        "campaignId": "c2", "name": "MEC | Creative Test", "optimizationGoal": "CONVERSATIONS",
        "destinationType": "MESSENGER", "cloneTargetingFromAdsetId": "as1", "dailyBudgetUsd": 15,
        "ageMin": 18, "ageMax": 65, "advantageAudience": 0,
    }), None)
    fields = response["data"]["graphRequest"]["fields"]

    assert response["approvalId"]
    assert fields["targeting"] == SOURCE_TARGETING
    assert response["data"]["targetingSource"] == {"adsetId": "as1", "name": "Vieja Chillona Ad"}
    assert any(c["rule"] == "targeting-source-ownership" and c["passed"] for c in response["ruleChecks"])


def test_create_adset_clone_from_other_account_is_blocked(graph, source_adset):
    source_adset["as9"] = {"id": "as9", "name": "x", "account_id": "777", "targeting": SOURCE_TARGETING}
    graph["objects"]["c2"] = {**CAMPAIGN, "id": "c2", "daily_budget": None}
    response = write.create_adset(_req({
        "campaignId": "c2", "name": "x", "optimizationGoal": "CONVERSATIONS",
        "cloneTargetingFromAdsetId": "as9", "dailyBudgetUsd": 15,
    }), None)

    assert "approvalId" not in response
    assert any(c["rule"] == "targeting-source-ownership" and not c["passed"] for c in response["ruleChecks"])


def test_create_adset_requires_geo_when_not_cloning(graph):
    graph["objects"]["c2"] = {**CAMPAIGN, "id": "c2", "daily_budget": None}
    with pytest.raises(AdsMcpError):
        write.create_adset(_req({
            "campaignId": "c2", "name": "x", "optimizationGoal": "CONVERSATIONS", "dailyBudgetUsd": 15,
        }), None)


def test_create_adset_blocks_budget_under_cbo_campaign(graph):
    response = write.create_adset(_req({
        "campaignId": "c1", "name": "x", "optimizationGoal": "CONVERSATIONS",
        "latitude": 34, "longitude": -118, "radiusMiles": 5, "dailyBudgetUsd": 10,
    }), None)

    assert "approvalId" not in response
    assert any(c["rule"] == "budget-level" and not c["passed"] for c in response["ruleChecks"])


def test_create_ad_creates_creative_then_ad(graph):
    graph["objects"]["as1"] = {"id": "as1", "name": "LA", "account_id": "123", "objectType": "adset"}
    payload = {"adsetId": "as1", "name": "Ad 1", "message": "Hola", "imageHash": "abc", "callToActionType": "MESSAGE_PAGE"}
    approval_id = write.create_ad(_req(payload), None)["approvalId"]

    response = write.create_ad(_req(payload, approval_id), None)

    assert [edge for edge, _ in graph["create"]] == ["adcreatives", "ads"]
    creative = graph["create"][0][1]["object_story_spec"]
    assert creative["page_id"] == "999"
    assert creative["link_data"]["call_to_action"] == {"type": "MESSAGE_PAGE", "value": {"app_destination": "MESSENGER"}}
    ad = graph["create"][1][1]
    assert ad["creative"] == {"creative_id": "new-adcreatives"} and ad["status"] == "PAUSED"
    assert response["data"]["result"] == {"creativeId": "new-adcreatives", "adId": "new-ads"}


def test_graph_request_sends_token_in_header_not_url(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"success": True}

    class Requests:
        @staticmethod
        def request(method, url, *, params, data, headers, timeout):
            captured.update(method=method, url=url, params=params, data=data, headers=headers)
            return Response()

    monkeypatch.setattr(meta_ads_client, "import_module", lambda name: Requests)

    meta_ads_client.update_object({**CONFIG, "api_version": "v23.0"}, object_id="c1", fields={"status": "PAUSED"})

    assert captured["headers"] == {"Authorization": "Bearer tok"}
    assert captured["url"] == "https://graph.facebook.com/v23.0/c1"
    assert "tok" not in captured["url"]
    assert "access_token" not in captured["params"] and "appsecret_proof" in captured["params"]
    assert captured["data"] == {"status": "PAUSED"}


def test_graph_error_surfaces_code_subcode_and_fbtrace(monkeypatch):
    class Response:
        status_code = 400
        text = ""

        def json(self):
            return {"error": {"message": "Invalid parameter", "type": "OAuthException", "code": 100,
                              "error_subcode": 1487390, "fbtrace_id": "TRACE"}}

    class Requests:
        @staticmethod
        def request(*args, **kwargs):
            return Response()

    monkeypatch.setattr(meta_ads_client, "import_module", lambda name: Requests)

    with pytest.raises(AdsMcpError) as exc:
        meta_ads_client.update_object(CONFIG, object_id="c1", fields={"status": "PAUSED"})

    assert exc.value.details["code"] == 100
    assert exc.value.details["subcode"] == 1487390
    assert exc.value.details["fbtraceId"] == "TRACE"


def test_object_snapshot_detects_type_from_nonexisting_field_errors(monkeypatch):
    def graph(config, method, path, *, params=None, data=None, tool=None):
        if "objective" in params["fields"] or "optimization_goal" in params["fields"]:
            raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", message="x",
                              details={"code": 100, "reason": "(#100) Tried accessing nonexisting field (objective)"})
        return {"id": path, "name": "Ad"}

    monkeypatch.setattr(meta_ads_client, "_graph_request", graph)

    assert meta_ads_client.get_object_snapshot(CONFIG, object_id="a1")["objectType"] == "ad"


LIVE_SCHEDULE = [{"days": [1, 2, 3, 4, 5], "start_minute": 720, "end_minute": 1320, "timezone_type": "ADVERTISER"}]


@pytest.fixture
def schedules(monkeypatch):
    adsets = {
        "as1": {"id": "as1", "name": "Vieja Chillona Ad", "account_id": "123", "adset_schedule": LIVE_SCHEDULE, "pacing_type": ["day_parting"]},
        "as2": {"id": "as2", "name": "MEC | Creative Test", "account_id": "123", "pacing_type": ["standard"], "daily_budget": "1500"},
        "as9": {"id": "as9", "name": "other", "account_id": "777"},
    }
    monkeypatch.setattr(write, "get_adset_schedule", lambda config, *, adset_id, tool: dict(adsets[adset_id]))
    return adsets


def test_update_adset_schedule_clones_source_schedule(graph, schedules):
    response = write.update_adset_schedule(_req({"adsetId": "as2", "cloneScheduleFromAdsetId": "as1"}), None)
    fields = response["data"]["graphRequest"]["fields"]

    assert response["approvalId"] and not graph["update"]
    assert fields == {"adset_schedule": LIVE_SCHEDULE, "pacing_type": ["day_parting"]}
    assert response["changes"][0]["before"] == {"adset_schedule": None, "pacing_type": ["standard"]}


def test_update_adset_schedule_executes_with_approval(graph, schedules):
    dry = write.update_adset_schedule(_req({"adsetId": "as2", "schedule": LIVE_SCHEDULE}), None)
    done = write.update_adset_schedule(_req({"adsetId": "as2", "schedule": LIVE_SCHEDULE}, dry["approvalId"]), None)

    assert done["executed"] is True
    assert graph["update"] == [("as2", {"adset_schedule": LIVE_SCHEDULE, "pacing_type": ["day_parting"]})]


@pytest.mark.parametrize("block", [
    {"days": [1], "start_minute": 750, "end_minute": 1320},   # not on the hour
    {"days": [1], "start_minute": 1320, "end_minute": 720},   # ends before it starts
    {"days": [7], "start_minute": 720, "end_minute": 1320},   # bad weekday
    {"days": [], "start_minute": 720, "end_minute": 1320},    # no days
])
def test_update_adset_schedule_rejects_invalid_blocks(graph, schedules, block):
    with pytest.raises(AdsMcpError):
        write.update_adset_schedule(_req({"adsetId": "as2", "schedule": [block]}), None)


def test_update_adset_schedule_blocks_other_account(graph, schedules):
    response = write.update_adset_schedule(_req({"adsetId": "as9", "schedule": LIVE_SCHEDULE}), None)

    assert "approvalId" not in response
    assert any(c["rule"] == "account-ownership" and not c["passed"] for c in response["ruleChecks"])


def test_update_adset_schedule_requires_a_schedule(graph, schedules):
    with pytest.raises(AdsMcpError):
        write.update_adset_schedule(_req({"adsetId": "as2"}), None)
