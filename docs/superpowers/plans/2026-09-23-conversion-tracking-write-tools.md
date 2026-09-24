# Conversion Tracking Write Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MCP write tools and site events so Mariachi El Cuis GA4 key events become valued Google
Ads conversions without using the web UIs.

**Architecture:**

- The Google Ads server gains two dry-run/execute write tools:
  - `google_ads_update_conversion_action`
  - `google_ads_set_auto_tagging`

  Their API calls live in `shared/google_ads_client.py`.
- The analytics server gains a new `tools/write.py`, using the GA4 Admin API v1beta, with two tools:
  - `analytics_create_key_event`
  - `analytics_link_google_ads`
- The Next.js site (`mariachi-cuis`) sends one distinctly named GA4 event per lead type:
  - `TrackEvent` sends the named events from the success pages.
  - A new `ClickTracker` sends `phone_click` and `whatsapp_click`.

**Tech Stack:**

- ads-mcp: Python, FastMCP, google-ads SDK (proto-plus), `google-analytics-admin` (`admin_v1beta`),
  pytest.
- Site: Next.js 15 / React, Vitest (node environment).

**Spec:** `docs/superpowers/specs/2026-09-23-conversion-tracking-write-tools-design.md`

## Global Constraints

**Write tools**

- Every write tool supports dry-run and execute. `dry_run=True` is the default. Execute without an
  `approval_id` → `BUSINESS_RULE_BLOCKED`. Execute when a rule check failed →
  `BUSINESS_RULE_BLOCKED`.
- Every write tool is idempotent. If the target state already holds, the response has
  `changes=[]`, `requiresConfirmation=False` and `executed=False`, and no API mutation is made.
- Upstream API failures → `AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", retryable=True)`.

**Values and credentials**

- Conversion values: `booking_confirmed` $50. `estimate_sent`, `contact_form_submit`,
  `whatsapp_click` and "Calls from ads" $5. `phone_click` is secondary with no value. The minimum
  call length for "Calls from ads" is 60 s.
- No credentials in code. The new OAuth scope is
  `https://www.googleapis.com/auth/analytics.edit`, and its token is stored **only** in El Cuis's
  analytics secret.
- No campaign, budget, keyword or geo changes.

**Code and repo**

- Code must run on Python 3.12, which is what CLAUDE.md requires. Tests currently run under the
  system `python` (3.13) with `python -m pytest`. `.venv` (used by the MCP servers) has no pytest.
  Don't use 3.13-only features.
- Test file basenames must be unique across `tests/`. `tests/` has no `__init__.py` files, so
  pytest's default import mode collides on duplicate basenames.
- Work directly on `main` in both repos (the user's explicit choice). Commit only the files each
  task touches, because `ads-mcp` has unrelated uncommitted work. Every commit message ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Site copy and behavior outside tracking is unchanged. Meta tracking (dataLayer events and
  `metaTrack`) must behave exactly as before.

## Review Focus

1. **Unsetting a boolean or zero value** (`primary_for_goal=False`, `default_value=0`) must still
   be sent to Google. `protobuf_helpers.field_mask(None, pb)` drops default values, so the helpers
   build explicit mask paths. → Task 2, `test_mutate_conversion_action_sends_explicit_mask_including_false`.
2. **Two conversion actions with the same name.** Updating the wrong one silently would be bad, so
   the tool must refuse. → Task 2, `test_conversion_snapshot_rejects_duplicate_names`.
3. **The dry-run passes on the read-only token, but execute is rejected** for scope. The user must
   get `AUTH_SCOPE_MISSING` with instructions, not a raw 403. → Task 4,
   `test_create_key_event_maps_insufficient_scope`.
4. **The key event already exists with a different counting method.** The tool must not claim
   success silently. It returns no change plus a warning. → Task 4,
   `test_existing_key_event_with_other_counting_method_warns`.
5. **Taps on an icon inside a `tel:` link**, links whose query string merely contains `wa.me`, and
   ordinary links must be classified correctly. → Task 6, `contact-clicks.test.ts`.

---

## File Structure

**ads-mcp**

| File | Responsibility |
|---|---|
| `tests/service_import.py` (create) | Imports one server's `tools` package in isolation. Every server has a top-level `tools` package. |
| `tests/meta_ads/test_read.py` (modify) | Uses the isolation helper. |
| `tests/google_ads/test_google_ads_read.py` (create) | Tests for the conversion-actions and change-history read fixes. |
| `tests/google_ads/test_google_ads_conversion_write.py` (create) | Conversion-action helpers and tool. |
| `tests/google_ads/test_google_ads_auto_tagging.py` (create) | Auto-tagging helpers and tool. |
| `tests/analytics/test_analytics_write.py` (create) | Key event and link tools. |
| `servers/google-ads/tools/read.py` (modify) | Conversion read fields; change-history ranges (already partly edited). |
| `shared/google_ads_client.py` (modify) | New snapshot and mutate helpers. |
| `servers/google-ads/tools/write.py` (modify) | `update_conversion_action`, `set_auto_tagging`. |
| `servers/google-ads/mcp_server.py` (modify) | Registers the two Google Ads tools. |
| `servers/analytics/tools/write.py` (create) | GA4 Admin client, `create_key_event`, `link_google_ads`. |
| `servers/analytics/mcp_server.py` (modify) | `_run_tool` and the two tool registrations. |
| `servers/analytics/requirements.txt` (modify) | Adds `google-analytics-admin`. |
| `scripts/get-refresh-token.py` (modify) | Adds the `analytics.edit` scope. |

**mariachi-cuis** (`C:\Users\cesar\Code\mariachi-cuis`)

| File | Responsibility |
|---|---|
| `src/lib/gtm.ts` (modify) | `trackConversion()`. |
| `src/lib/gtm.test.ts` (modify) | Tests for `trackConversion`. |
| `src/components/analytics/track-event.tsx` (modify) | Uses `trackConversion`. |
| `src/lib/contact-clicks.ts` (create) | `contactClickEvent()`, `handleContactClick()`. |
| `src/lib/contact-clicks.test.ts` (create) | Tests for the click classification. |
| `src/components/analytics/click-tracker.tsx` (create) | The document listener. |
| `src/app/[lang]/layout.tsx` (modify) | Mounts `<ClickTracker />`. |

---

### Task 1: Test import isolation + commit the audit read fixes

**Files:**

- Create: `tests/service_import.py`
- Create: `tests/google_ads/test_google_ads_read.py`
- Modify: `tests/meta_ads/test_read.py:1-15` (imports only)
- Modify: `servers/google-ads/tools/read.py` (`get_conversion_actions`, ~lines 1128-1156; the change-history edits are already in the working tree)
- Commit also: `servers/google-ads/mcp_server.py`. Its only uncommitted change is the change-history `date_range` description. Verify with `git diff servers/google-ads/mcp_server.py` before staging.

**Interfaces:**

- Produces: `tests.service_import.load_service_module(service: str, module: str) -> ModuleType`.
  Later tasks load `tools.read`, `tools.write` and `mcp_server` for `"google-ads"` and
  `"analytics"` through it.
- Produces: rows from `get_conversion_actions` gain the keys `"primary_for_goal": bool` and
  `"phone_call_duration_seconds": int`.

- [ ] **Step 1: Create the isolation helper**

`tests/service_import.py`:

```python
"""Import one server's modules in isolation for tests.

Every server under servers/ ships its own top-level `tools` package (and
`mcp_server` module). Once one is imported, Python reuses it for every later
`import tools`, so a test run that touches two servers would silently get the
wrong one. This helper drops the cached copies and puts the requested
server's directory first on sys.path before importing.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SERVERS = ROOT / "servers"
_SHADOWED = ("tools", "mcp_server", "main")


def load_service_module(service: str, module: str) -> ModuleType:
    for name in list(sys.modules):
        if name in _SHADOWED or name.startswith("tools."):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if not p.startswith(str(SERVERS))]
    sys.path.insert(0, str(SERVERS / service))
    return importlib.import_module(module)
```

- [ ] **Step 2: Switch the Meta test to the helper**

In `tests/meta_ads/test_read.py`, replace:

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
META_SERVICE = ROOT / "servers" / "meta-ads"
if str(META_SERVICE) not in sys.path:
    sys.path.insert(0, str(META_SERVICE))

from shared import meta_ads_client
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.runtime_config import load_meta_ads_config
from tools import read
```

with:

```python
from shared import meta_ads_client
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.runtime_config import load_meta_ads_config
from tests.service_import import load_service_module

read = load_service_module("meta-ads", "tools.read")
```

If the file still uses `sys` or `Path` further down, keep those imports.

- [ ] **Step 3: Write the failing read tests**

`tests/google_ads/test_google_ads_read.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from shared.models import ToolRequest
from tests.service_import import load_service_module

read = load_service_module("google-ads", "tools.read")


def _enum(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


class _FakeGoogleAdsService:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    def search(self, *, customer_id, query):
        self.queries.append(query)
        return iter(self.rows)


class _FakeClient:
    def __init__(self, service):
        self.service = service

    def get_service(self, name):
        assert name == "GoogleAdsService"
        return self.service


def _patch_client(monkeypatch, rows):
    service = _FakeGoogleAdsService(rows)
    monkeypatch.setattr(read, "load_google_ads_sdk_config", lambda **kw: {"customer_account_id": "123"})
    monkeypatch.setattr(read, "_resolve_working_client", lambda config, tool: (_FakeClient(service), "123"))
    return service


def test_get_conversion_actions_uses_gaql_type_field_and_returns_goal_fields(monkeypatch):
    row = SimpleNamespace(conversion_action=SimpleNamespace(
        id=7790452537,
        name="Calls from ads",
        status=_enum("ENABLED"),
        type_=_enum("AD_CALL"),
        category=_enum("PHONE_CALL_LEAD"),
        counting_type=_enum("MANY_PER_CLICK"),
        include_in_conversions_metric=True,
        primary_for_goal=True,
        phone_call_duration_seconds=60,
        value_settings=SimpleNamespace(default_value=1.0),
    ))
    service = _patch_client(monkeypatch, [row])

    response = read.get_conversion_actions(ToolRequest(businessKey="el-cuis"), None)

    query = service.queries[0]
    assert "conversion_action.type," in query
    assert "type_" not in query
    assert "conversion_action.primary_for_goal" in query
    assert "conversion_action.phone_call_duration_seconds" in query
    assert response["data"]["rows"] == [{
        "conversion_id": "7790452537",
        "name": "Calls from ads",
        "status": "ENABLED",
        "type": "AD_CALL",
        "category": "PHONE_CALL_LEAD",
        "counting_type": "MANY_PER_CLICK",
        "included_in_conversions": True,
        "primary_for_goal": True,
        "phone_call_duration_seconds": 60,
        "default_value": 1.0,
    }]


def test_change_history_rejects_ranges_older_than_30_days(monkeypatch):
    service = _patch_client(monkeypatch, [])

    for requested in ("LAST_30_DAYS", "LAST_MONTH", "BOGUS"):
        read.get_change_history(ToolRequest(businessKey="el-cuis", payload={"dateRange": requested}), None)
    read.get_change_history(ToolRequest(businessKey="el-cuis", payload={"dateRange": "THIS_MONTH"}), None)

    assert all("DURING LAST_14_DAYS" in q for q in service.queries[:3])
    assert "DURING THIS_MONTH" in service.queries[3]


def test_change_history_summary_reads_cleanly(monkeypatch):
    _patch_client(monkeypatch, [])

    response = read.get_change_history(ToolRequest(businessKey="el-cuis", payload={"dateRange": "LAST_7_DAYS"}), None)

    assert response["summary"] == "0 changes (last 7 days)."
```

- [ ] **Step 4: Run the tests to verify the first one fails**

Run: `cd C:/Users/cesar/Code/ads-mcp && python -m pytest tests/google_ads/test_google_ads_read.py -v`

Expected:

- `test_get_conversion_actions_...` FAILS on `"conversion_action.primary_for_goal" in query`.
- The two change-history tests PASS, because those edits are already in the working tree.

- [ ] **Step 5: Add the goal fields to `get_conversion_actions`**

In `servers/google-ads/tools/read.py`, `get_conversion_actions`, the SELECT becomes:

```python
    query = """
        SELECT
          conversion_action.id,
          conversion_action.name,
          conversion_action.status,
          conversion_action.type,
          conversion_action.category,
          conversion_action.counting_type,
          conversion_action.include_in_conversions_metric,
          conversion_action.primary_for_goal,
          conversion_action.phone_call_duration_seconds,
          conversion_action.value_settings.default_value
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        ORDER BY conversion_action.name ASC
    """
```

The row dict becomes:

```python
            rows.append({
                "conversion_id": str(row.conversion_action.id),
                "name": row.conversion_action.name,
                "status": row.conversion_action.status.name,
                "type": row.conversion_action.type_.name,
                "category": row.conversion_action.category.name,
                "counting_type": row.conversion_action.counting_type.name,
                "included_in_conversions": row.conversion_action.include_in_conversions_metric,
                "primary_for_goal": row.conversion_action.primary_for_goal,
                "phone_call_duration_seconds": row.conversion_action.phone_call_duration_seconds,
                "default_value": row.conversion_action.value_settings.default_value,
            })
```

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q`
Expected: all tests pass (36 existing + 3 new). The Meta tests must still pass, which proves the
isolation helper works across servers.

- [ ] **Step 7: Live smoke check (read-only)**

Run:

```bash
cd C:/Users/cesar/Code/ads-mcp/servers/google-ads && ../../.venv/Scripts/python.exe -c "import sys,json; sys.path.insert(0,'../..'); import mcp_server as m; print(json.dumps(m.google_ads_get_conversion_actions('el-cuis')['data']['rows'], indent=1))"
```

Expected: 2 rows ("Calls from ads", "Clicks to call"), each with `primary_for_goal` and
`phone_call_duration_seconds`.

- [ ] **Step 8: Commit**

```bash
cd C:/Users/cesar/Code/ads-mcp
git add tests/service_import.py tests/meta_ads/test_read.py tests/google_ads/test_google_ads_read.py servers/google-ads/tools/read.py servers/google-ads/mcp_server.py
git commit -m "fix(google-ads): repair conversion-actions and change-history queries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `google_ads_update_conversion_action`

**Files:**

- Modify: `shared/google_ads_client.py` (append the helpers)
- Modify: `servers/google-ads/tools/write.py` (imports + new function at end)
- Modify: `servers/google-ads/mcp_server.py` (import + tool registration after `google_ads_add_keyword`)
- Test: `tests/google_ads/test_google_ads_conversion_write.py`

**Interfaces:**

- Consumes: `tests.service_import.load_service_module` (Task 1).
- Produces, in `shared.google_ads_client`:
  - `get_conversion_action_snapshot(config: dict, *, conversion_name: str, tool: str | None = None) -> dict`
    with keys `customerAccountId, resourceName, conversionId, name, status, type, primaryForGoal,
    phoneCallDurationSeconds, defaultValue, alwaysUseDefaultValue`.
  - `mutate_conversion_action(config: dict, *, resource_name: str, updates: dict[str, Any], tool: str | None = None) -> dict`.
    The `updates` keys are field-mask paths, drawn from `status`, `primary_for_goal`,
    `value_settings.default_value`, `value_settings.always_use_default_value` and
    `phone_call_duration_seconds`.
- Produces, in `tools.write`: `update_conversion_action(request: ToolRequest, request_id: str | None) -> dict`.
  It reads the payload keys `conversionName, status, primaryForGoal, defaultValue,
  phoneCallDurationSeconds`.
- Produces the MCP tool `google_ads_update_conversion_action(business_key, conversion_name,
  status=None, primary_for_goal=None, default_value=None, phone_call_duration_seconds=None,
  dry_run=True, approval_id=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/google_ads/test_google_ads_conversion_write.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/google_ads/test_google_ads_conversion_write.py -v`
Expected: every test FAILS with `AttributeError`. `get_conversion_action_snapshot`,
`mutate_conversion_action` and `update_conversion_action` don't exist yet.

- [ ] **Step 3: Add the helpers to `shared/google_ads_client.py`**

Append:

```python
def get_conversion_action_snapshot(
    config: dict[str, Any],
    *,
    conversion_name: str,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          conversion_action.resource_name,
          conversion_action.id,
          conversion_action.name,
          conversion_action.status,
          conversion_action.type,
          conversion_action.primary_for_goal,
          conversion_action.phone_call_duration_seconds,
          conversion_action.value_settings.default_value,
          conversion_action.value_settings.always_use_default_value
        FROM conversion_action
        WHERE conversion_action.name = '{_escape_gaql_string(conversion_name)}'
          AND conversion_action.status != 'REMOVED'
    """

    try:
        rows = list(google_ads_service.search(customer_id=customer_id, query=query))
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads conversion action lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if not rows:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Conversion action '{conversion_name}' was not found in Google Ads.",
            tool=tool,
            details={"conversionName": conversion_name},
        )
    if len(rows) > 1:
        raise AdsMcpError(
            status_code=409,
            error_code="REQUEST_INVALID",
            message=(
                f"{len(rows)} conversion actions are named '{conversion_name}'. "
                "Rename one in Google Ads so the update can't hit the wrong one."
            ),
            tool=tool,
            details={"conversionName": conversion_name},
        )

    action = rows[0].conversion_action
    return {
        "customerAccountId": customer_id,
        "resourceName": action.resource_name,
        "conversionId": str(action.id),
        "name": action.name,
        "status": action.status.name,
        "type": action.type_.name,
        "primaryForGoal": bool(action.primary_for_goal),
        "phoneCallDurationSeconds": int(action.phone_call_duration_seconds),
        "defaultValue": float(action.value_settings.default_value),
        "alwaysUseDefaultValue": bool(action.value_settings.always_use_default_value),
    }


_CONVERSION_UPDATE_PATHS = (
    "status",
    "primary_for_goal",
    "value_settings.default_value",
    "value_settings.always_use_default_value",
    "phone_call_duration_seconds",
)


def mutate_conversion_action(
    config: dict[str, Any],
    *,
    resource_name: str,
    updates: dict[str, Any],
    tool: str | None = None,
) -> dict[str, Any]:
    unknown = [path for path in updates if path not in _CONVERSION_UPDATE_PATHS]
    if unknown:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"Unsupported conversion action fields: {', '.join(unknown)}.",
            tool=tool,
        )

    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)

    try:
        conversion_service = client.get_service("ConversionActionService")
        operation = client.get_type("ConversionActionOperation")
        action = operation.update
        action.resource_name = resource_name
        for path, value in updates.items():
            if path == "status":
                action.status = getattr(client.enums.ConversionActionStatusEnum, value)
            elif path == "value_settings.default_value":
                action.value_settings.default_value = value
            elif path == "value_settings.always_use_default_value":
                action.value_settings.always_use_default_value = value
            else:
                setattr(action, path, value)
        # Explicit paths: protobuf_helpers.field_mask() diffs against defaults
        # and would silently drop updates like primary_for_goal=False.
        operation.update_mask.paths.extend(updates.keys())
        response = conversion_service.mutate_conversion_actions(
            customer_id=customer_id,
            operations=[operation],
        )
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads conversion action update failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    result = response.results[0] if response.results else None
    return {
        "customerAccountId": customer_id,
        "resourceName": getattr(result, "resource_name", resource_name),
        "updatedFields": list(updates.keys()),
    }
```

- [ ] **Step 4: Add `update_conversion_action` to `servers/google-ads/tools/write.py`**

Add these two names to the existing `from shared.google_ads_client import (...)` block:
`get_conversion_action_snapshot` and `mutate_conversion_action`. Then append:

```python
_CONVERSION_STATUSES = {"ENABLED", "HIDDEN"}


def _conversion_request_error(message: str) -> AdsMcpError:
    return AdsMcpError(
        status_code=400,
        error_code="REQUEST_INVALID",
        message=message,
        tool="update_conversion_action",
    )


def update_conversion_action(request, request_id: str | None) -> dict:
    tool = "update_conversion_action"
    payload = request.payload or {}
    conversion_name = payload.get("conversionName")
    if not conversion_name:
        raise _conversion_request_error("conversionName is required.")

    status = payload.get("status")
    primary_for_goal = payload.get("primaryForGoal")
    default_value = payload.get("defaultValue")
    call_duration = payload.get("phoneCallDurationSeconds")

    if all(v is None for v in (status, primary_for_goal, default_value, call_duration)):
        raise _conversion_request_error(
            "Provide at least one of status, primaryForGoal, defaultValue, phoneCallDurationSeconds."
        )
    if status is not None:
        status = str(status).upper()
        if status not in _CONVERSION_STATUSES:
            raise _conversion_request_error("status must be 'ENABLED' or 'HIDDEN'.")
    if default_value is not None:
        try:
            default_value = float(default_value)
        except (TypeError, ValueError):
            raise _conversion_request_error("defaultValue must be a number.") from None
        if default_value < 0:
            raise _conversion_request_error("defaultValue cannot be negative.")
    if call_duration is not None:
        try:
            call_duration = int(call_duration)
        except (TypeError, ValueError):
            raise _conversion_request_error("phoneCallDurationSeconds must be a whole number.") from None
        if call_duration < 0:
            raise _conversion_request_error("phoneCallDurationSeconds cannot be negative.")

    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        payload={},
        change_type="conversion_action",
    )

    config = load_google_ads_sdk_config(business_key=request.businessKey, tool=tool)
    snapshot = get_conversion_action_snapshot(config, conversion_name=conversion_name, tool=tool)

    if call_duration is not None and snapshot["type"] != "AD_CALL":
        raise _conversion_request_error(
            f"phoneCallDurationSeconds only applies to call conversions (AD_CALL); "
            f"'{conversion_name}' is {snapshot['type']}."
        )

    # (mask path, label, before, after)
    candidates: list[tuple[str, str, object, object]] = []
    if status is not None:
        candidates.append(("status", "Status", snapshot["status"], status))
    if primary_for_goal is not None:
        candidates.append(("primary_for_goal", "Primary goal", snapshot["primaryForGoal"], bool(primary_for_goal)))
    if default_value is not None:
        candidates.append(("value_settings.default_value", "Default value", snapshot["defaultValue"], default_value))
        # GA4 events carry no value, so always apply the default.
        candidates.append((
            "value_settings.always_use_default_value", "Always use default value",
            snapshot["alwaysUseDefaultValue"], True,
        ))
    if call_duration is not None:
        candidates.append((
            "phone_call_duration_seconds", "Minimum call length (seconds)",
            snapshot["phoneCallDurationSeconds"], call_duration,
        ))

    pending = [c for c in candidates if c[2] != c[3]]
    is_dry_run = request.dryRun is not False
    data = {"conversionId": snapshot["conversionId"], "type": snapshot["type"], "resourceName": snapshot["resourceName"]}

    if not pending:
        return build_success_response(
            service=SERVICE_NAME,
            tool=tool,
            mode="dry-run" if is_dry_run else "execute",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"No change: {conversion_name} already matches.",
            rule_checks=rule_checks,
            changes=[],
            data=data,
            requires_confirmation=False,
            executed=False,
        )

    changes = [
        build_change(
            field=f"conversion_action.{path}",
            label=label,
            before=before,
            after=after,
            status="proposed",
            resource_type="conversion_action",
            resource_id=snapshot["conversionId"],
        )
        for path, label, before, after in pending
    ]

    if is_dry_run:
        return build_success_response(
            service=SERVICE_NAME,
            tool=tool,
            mode="dry-run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would update {len(changes)} setting(s) on {conversion_name}.",
            rule_checks=rule_checks,
            changes=changes,
            data=data,
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
            tool=tool,
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool=tool,
        )

    mutation_result = mutate_conversion_action(
        config,
        resource_name=snapshot["resourceName"],
        updates={path: after for path, _, _, after in pending},
        tool=tool,
    )

    for change in changes:
        change["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool=tool,
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Updated {len(changes)} setting(s) on {conversion_name}.",
        rule_checks=rule_checks,
        changes=changes,
        data={**data, **mutation_result},
        requires_confirmation=False,
        executed=True,
    )
```

The spec's "reject non-status/primary/value fields on GA4 conversions" rule holds by
construction. The tool accepts no other fields, and `phoneCallDurationSeconds` is rejected on
anything but `AD_CALL`, which covers GA4 types.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/google_ads/test_google_ads_conversion_write.py -v`
Expected: all PASS.

- [ ] **Step 6: Register the MCP tool**

In `servers/google-ads/mcp_server.py`, add `update_conversion_action,` to the `from tools.write
import (...)` block. After the `google_ads_add_keyword` tool function, add:

```python
@mcp.tool()
def google_ads_update_conversion_action(
    business_key: Annotated[str, Field(description="Business key for any onboarded client, e.g. 'rnr-electrician', 'gq-painting', 'el-cuis' — not limited to these examples.")],
    conversion_name: Annotated[str, Field(description="Exact conversion action name as shown in Google Ads, e.g. 'Calls from ads' or an imported GA4 event like 'booking_confirmed'")],
    status: Annotated[str | None, Field(description="'ENABLED' (e.g. to turn on a HIDDEN imported GA4 conversion) or 'HIDDEN'")] = None,
    primary_for_goal: Annotated[bool | None, Field(description="True = Primary (used for bidding), False = Secondary (reporting only)")] = None,
    default_value: Annotated[float | None, Field(description="Conversion value in account currency. Also turns on 'always use default value'.", ge=0)] = None,
    phone_call_duration_seconds: Annotated[int | None, Field(description="Call conversions (AD_CALL) only: minimum call length in seconds to count", ge=0)] = None,
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Update a conversion action's status, primary/secondary goal, value, or minimum call length.

    Only the fields you pass are changed. Imported GA4 conversions start as HIDDEN;
    set status='ENABLED' to import them.

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(
        businessKey=business_key,
        dryRun=dry_run,
        approvalId=approval_id,
        payload={
            "conversionName": conversion_name,
            "status": status,
            "primaryForGoal": primary_for_goal,
            "defaultValue": default_value,
            "phoneCallDurationSeconds": phone_call_duration_seconds,
        },
    )
    return _run_tool("google_ads_update_conversion_action", lambda: update_conversion_action(req, request_id=None))
```

- [ ] **Step 7: Live dry-run smoke check (no writes)**

Run:

```bash
cd C:/Users/cesar/Code/ads-mcp/servers/google-ads && ../../.venv/Scripts/python.exe -c "import sys,json; sys.path.insert(0,'../..'); import mcp_server as m; print(json.dumps(m.google_ads_update_conversion_action('el-cuis','Calls from ads', phone_call_duration_seconds=60, default_value=5), indent=1))"
```

Expected: `ok: true`, `mode: dry-run`, `executed: false`, and a changes list that includes the
default value (1.0 → 5.0). **Do not run with `dry_run=False`.** Execution happens only in the
rollout, with user approval.

- [ ] **Step 8: Full suite and commit**

Run: `python -m pytest -q` → all pass.

```bash
cd C:/Users/cesar/Code/ads-mcp
git add shared/google_ads_client.py servers/google-ads/tools/write.py servers/google-ads/mcp_server.py tests/google_ads/test_google_ads_conversion_write.py
git commit -m "feat(google-ads): add update_conversion_action write tool

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `google_ads_set_auto_tagging`

**Files:**

- Modify: `shared/google_ads_client.py` (append)
- Modify: `servers/google-ads/tools/write.py` (imports + append)
- Modify: `servers/google-ads/mcp_server.py` (import + registration after `google_ads_update_conversion_action`)
- Test: `tests/google_ads/test_google_ads_auto_tagging.py`

**Interfaces:**

- Consumes: `load_service_module` (Task 1) and the write-tool style from Task 2.
- Produces, in `shared.google_ads_client`:
  - `get_customer_settings_snapshot(config: dict, *, tool: str | None = None) -> dict` with keys
    `customerAccountId, resourceName, autoTaggingEnabled`.
  - `mutate_customer_auto_tagging(config: dict, *, resource_name: str, enabled: bool, tool: str | None = None) -> dict`.
- Produces, in `tools.write`: `set_auto_tagging(request, request_id) -> dict`, which reads the
  payload key `enabled` (must be a bool).
- Produces the MCP tool `google_ads_set_auto_tagging(business_key, enabled, dry_run=True, approval_id=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/google_ads/test_google_ads_auto_tagging.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/google_ads/test_google_ads_auto_tagging.py -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append the helpers to `shared/google_ads_client.py`**

```python
def get_customer_settings_snapshot(config: dict[str, Any], *, tool: str | None = None) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)
    google_ads_service = client.get_service("GoogleAdsService")
    query = "SELECT customer.resource_name, customer.auto_tagging_enabled FROM customer LIMIT 1"

    try:
        row = next(iter(google_ads_service.search(customer_id=customer_id, query=query)), None)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads customer settings lookup failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    if row is None:
        raise AdsMcpError(
            status_code=404,
            error_code="REQUEST_INVALID",
            message=f"Google Ads customer {customer_id} was not found.",
            tool=tool,
        )

    return {
        "customerAccountId": customer_id,
        "resourceName": row.customer.resource_name,
        "autoTaggingEnabled": bool(row.customer.auto_tagging_enabled),
    }


def mutate_customer_auto_tagging(
    config: dict[str, Any],
    *,
    resource_name: str,
    enabled: bool,
    tool: str | None = None,
) -> dict[str, Any]:
    client = build_google_ads_client(config, tool=tool)
    customer_id = get_google_ads_customer_id(config, tool=tool)

    try:
        customer_service = client.get_service("CustomerService")
        operation = client.get_type("CustomerOperation")
        operation.update.resource_name = resource_name
        operation.update.auto_tagging_enabled = enabled
        # Explicit path so enabled=False is not dropped by default-diffing.
        operation.update_mask.paths.append("auto_tagging_enabled")
        response = customer_service.mutate_customer(customer_id=customer_id, operation=operation)
    except Exception as exc:
        raise AdsMcpError(
            status_code=502,
            error_code="UPSTREAM_ERROR",
            message="Google Ads auto-tagging update failed.",
            tool=tool,
            retryable=True,
            details={"reason": str(exc)},
        ) from exc

    result = getattr(response, "result", None)
    return {
        "customerAccountId": customer_id,
        "resourceName": getattr(result, "resource_name", None) or resource_name,
        "autoTaggingEnabled": enabled,
    }
```

- [ ] **Step 4: Append `set_auto_tagging` to `servers/google-ads/tools/write.py`**

Add `get_customer_settings_snapshot` and `mutate_customer_auto_tagging` to the
`shared.google_ads_client` import block. Then append:

```python
def set_auto_tagging(request, request_id: str | None) -> dict:
    tool = "set_auto_tagging"
    enabled = (request.payload or {}).get("enabled")
    if not isinstance(enabled, bool):
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message="enabled must be true or false.",
            tool=tool,
        )

    rule_checks = evaluate_google_ads_mutation_rules(
        business_key=request.businessKey,
        payload={},
        change_type="account_setting",
    )

    config = load_google_ads_sdk_config(business_key=request.businessKey, tool=tool)
    snapshot = get_customer_settings_snapshot(config, tool=tool)
    is_dry_run = request.dryRun is not False
    state = "on" if enabled else "off"

    if snapshot["autoTaggingEnabled"] == enabled:
        return build_success_response(
            service=SERVICE_NAME,
            tool=tool,
            mode="dry-run" if is_dry_run else "execute",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"No change: auto-tagging is already {state}.",
            rule_checks=rule_checks,
            changes=[],
            data=snapshot,
            requires_confirmation=False,
            executed=False,
        )

    changes = [
        build_change(
            field="customer.auto_tagging_enabled",
            label="Auto-tagging",
            before=snapshot["autoTaggingEnabled"],
            after=enabled,
            status="proposed",
            resource_type="customer",
            resource_id=snapshot["customerAccountId"],
        )
    ]

    if is_dry_run:
        return build_success_response(
            service=SERVICE_NAME,
            tool=tool,
            mode="dry-run",
            business_key=request.businessKey,
            request_id=request_id,
            summary=f"Would turn auto-tagging {state}.",
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
            tool=tool,
        )

    if any(not check["passed"] for check in rule_checks):
        raise AdsMcpError(
            status_code=400,
            error_code="BUSINESS_RULE_BLOCKED",
            message="Execution blocked by business rules.",
            retryable=False,
            rule_checks=rule_checks,
            tool=tool,
        )

    mutation_result = mutate_customer_auto_tagging(
        config, resource_name=snapshot["resourceName"], enabled=enabled, tool=tool,
    )
    changes[0]["status"] = "applied"
    return build_success_response(
        service=SERVICE_NAME,
        tool=tool,
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Turned auto-tagging {state}.",
        rule_checks=rule_checks,
        changes=changes,
        data=mutation_result,
        requires_confirmation=False,
        executed=True,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/google_ads/test_google_ads_auto_tagging.py -v` → all PASS.

- [ ] **Step 6: Register the MCP tool**

In `servers/google-ads/mcp_server.py`, add `set_auto_tagging,` to the `tools.write` import. After
`google_ads_update_conversion_action`, add:

```python
@mcp.tool()
def google_ads_set_auto_tagging(
    business_key: Annotated[str, Field(description="Business key for any onboarded client, e.g. 'rnr-electrician', 'gq-painting', 'el-cuis' — not limited to these examples.")],
    enabled: Annotated[bool, Field(description="True to turn account auto-tagging (gclid) on, False to turn it off")],
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Turn Google Ads account auto-tagging on or off. GA4 conversion import needs it on.

    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(businessKey=business_key, dryRun=dry_run, approvalId=approval_id, payload={"enabled": enabled})
    return _run_tool("google_ads_set_auto_tagging", lambda: set_auto_tagging(req, request_id=None))
```

- [ ] **Step 7: Live dry-run smoke check (no writes)**

```bash
cd C:/Users/cesar/Code/ads-mcp/servers/google-ads && ../../.venv/Scripts/python.exe -c "import sys,json; sys.path.insert(0,'../..'); import mcp_server as m; print(json.dumps(m.google_ads_set_auto_tagging('el-cuis', True), indent=1))"
```

Expected: `ok: true`. The response is either a dry-run with one proposed change or "No change:
auto-tagging is already on." Record which one.

- [ ] **Step 8: Full suite and commit**

Run: `python -m pytest -q` → all pass.

```bash
git add shared/google_ads_client.py servers/google-ads/tools/write.py servers/google-ads/mcp_server.py tests/google_ads/test_google_ads_auto_tagging.py
git commit -m "feat(google-ads): add set_auto_tagging write tool

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: GA4 Admin client + `analytics_create_key_event` + OAuth scope

**Files:**

- Create: `servers/analytics/tools/write.py`
- Modify: `servers/analytics/mcp_server.py`
- Modify: `servers/analytics/requirements.txt`
- Modify: `scripts/get-refresh-token.py:31-38` (the scopes list)
- Test: `tests/analytics/test_analytics_write.py`

**Interfaces:**

- Consumes: `load_service_module` (Task 1).
- Produces, in analytics `tools.write`:
  - `_load_analytics_config(business_key: str, tool: str) -> dict`
  - `_build_admin_client(config: dict)`, which returns an object with `list_key_events`,
    `create_key_event`, `list_google_ads_links` and `create_google_ads_link`
  - `_upstream_error(exc: Exception, *, tool: str, action: str) -> AdsMcpError`
  - `create_key_event(request, request_id) -> dict`, which reads the payload keys `eventName` and
    `countingMethod`

  Task 5 reuses the three private helpers.
- Produces the MCP tool `analytics_create_key_event(business_key, event_name,
  counting_method="ONCE_PER_SESSION", dry_run=True, approval_id=None)`.

- [ ] **Step 1: Install the GA4 Admin SDK in both interpreters**

```bash
cd C:/Users/cesar/Code/ads-mcp
python -m pip install "google-analytics-admin>=0.24.0"
.venv/Scripts/python.exe -m pip install "google-analytics-admin>=0.24.0"
python -c "from google.analytics.admin_v1beta.types import KeyEvent, GoogleAdsLink; print(KeyEvent.CountingMethod.ONCE_PER_SESSION.name)"
```

Expected: the last command prints `ONCE_PER_SESSION`. Append `google-analytics-admin>=0.24.0` to
`servers/analytics/requirements.txt`.

- [ ] **Step 2: Write the failing tests**

`tests/analytics/test_analytics_write.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/analytics/test_analytics_write.py -v`
Expected: FAIL. The module `tools.write` isn't found for analytics.

- [ ] **Step 4: Create `servers/analytics/tools/write.py`**

```python
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
```

`load_google_ads_config` and `GoogleAdsLink` are imported now, so Task 5 can add its function
without touching the imports. Both stay unused until Task 5, which is expected.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/analytics/test_analytics_write.py -v` → all PASS.

- [ ] **Step 6: Register the MCP tool, with a `_run_tool` wrapper**

In `servers/analytics/mcp_server.py`:

1. Change `from typing import Annotated` to `from typing import Annotated, Callable`.
2. Add `from shared.errors import AdsMcpError`.
3. Add `from tools.write import create_key_event`.
4. Below the `mcp = FastMCP(...)` block, add:

```python
def _run_tool(tool_name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except AdsMcpError as exc:
        return exc.to_response(service="analytics", request_id=None)
    except Exception as exc:
        return AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="Unhandled tool error.",
            tool=tool_name,
            details={"reason": str(exc)},
        ).to_response(service="analytics", request_id=None)
```

5. After `analytics_get_top_pages`, add:

```python
@mcp.tool()
def analytics_create_key_event(
    business_key: Annotated[str, Field(description="Business key for any onboarded client, e.g. 'rnr-electrician', 'gq-painting', 'el-cuis' — not limited to these examples.")],
    event_name: Annotated[str, Field(description="GA4 event name to mark as a key event, e.g. 'booking_confirmed'")],
    counting_method: Annotated[str, Field(description="ONCE_PER_SESSION (one conversion per visit; recommended for leads) or ONCE_PER_EVENT")] = "ONCE_PER_SESSION",
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Mark a GA4 event as a key event so Google Ads can import it as a conversion.

    Requires the analytics.edit OAuth scope for execute.
    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(businessKey=business_key, dryRun=dry_run, approvalId=approval_id,
                      payload={"eventName": event_name, "countingMethod": counting_method})
    return _run_tool("analytics_create_key_event", lambda: create_key_event(req, request_id=None))
```

6. Change the `instructions=` string. Append: `"Write tools (key events, Google Ads link) always
   run dry_run=true first and need explicit user approval before dry_run=false. "`

- [ ] **Step 7: Add the scope to the refresh-token script**

In `scripts/get-refresh-token.py`, the `scopes=[...]` list becomes:

```python
    scopes=[
        "https://www.googleapis.com/auth/adwords",
        "https://www.googleapis.com/auth/analytics.readonly",
        "https://www.googleapis.com/auth/analytics.edit",
        "https://www.googleapis.com/auth/webmasters.readonly",
        "https://www.googleapis.com/auth/business.manage",
        "openid",
        "email",
    ],
```

Update the module docstring's first line to `Get a Google OAuth refresh token for Google Ads / GA4
(read + admin edit) / Search Console / GBP.`

- [ ] **Step 8: Live dry-run smoke check (read-only token is fine for dry-run)**

```bash
cd C:/Users/cesar/Code/ads-mcp/servers/analytics && ../../.venv/Scripts/python.exe -c "import sys,json; sys.path.insert(0,'../..'); import mcp_server as m; print(json.dumps(m.analytics_create_key_event('el-cuis','booking_confirmed'), indent=1))"
```

Expected: `ok: true`, `mode: dry-run`, and `existingKeyEvents` (probably `[]`, or `generate_lead`
if the user marked it). If the response is an `UPSTREAM_ERROR` whose details say the API is
disabled, the Google Analytics Admin API must be enabled in the Google Cloud project that owns the
OAuth client. Report the exact message to the user and stop.

- [ ] **Step 9: Full suite and commit**

Run: `python -m pytest -q` → all pass.

```bash
git add servers/analytics/tools/write.py servers/analytics/mcp_server.py servers/analytics/requirements.txt scripts/get-refresh-token.py tests/analytics/test_analytics_write.py
git commit -m "feat(analytics): add GA4 create_key_event write tool and analytics.edit scope

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `analytics_link_google_ads`

**Files:**

- Modify: `servers/analytics/tools/write.py` (append)
- Modify: `servers/analytics/mcp_server.py` (import + registration)
- Test: `tests/analytics/test_analytics_write.py` (append)

**Interfaces:**

- Consumes, from Task 4: `_load_analytics_config`, `_build_admin_client`, `_property`,
  `_upstream_error`, `_require_approval`, the `admin` fixture and `_FakeAdmin`.
- Consumes: `shared.runtime_config.load_google_ads_config(*, business_key, tool) -> dict`, which
  contains `customer_account_id`.
- Produces: `link_google_ads(request, request_id) -> dict` and the MCP tool
  `analytics_link_google_ads(business_key, dry_run=True, approval_id=None)`.

- [ ] **Step 1: Append the failing tests**

Append to `tests/analytics/test_analytics_write.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/analytics/test_analytics_write.py -v -k link`
Expected: FAIL with `AttributeError: ... has no attribute 'link_google_ads'`.

- [ ] **Step 3: Append `link_google_ads` to `servers/analytics/tools/write.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/analytics/test_analytics_write.py -v` → all PASS.

- [ ] **Step 5: Register the MCP tool**

In `servers/analytics/mcp_server.py`, change the import to
`from tools.write import create_key_event, link_google_ads`. After `analytics_create_key_event`,
add:

```python
@mcp.tool()
def analytics_link_google_ads(
    business_key: Annotated[str, Field(description="Business key for any onboarded client, e.g. 'rnr-electrician', 'gq-painting', 'el-cuis' — not limited to these examples.")],
    dry_run: Annotated[bool, Field(description="If true, shows proposed changes without applying them. Always use true first.")] = True,
    approval_id: Annotated[str | None, Field(description="Required for execute mode (dry_run=false). Copy from the dry-run response.")] = None,
) -> dict:
    """Link the client's GA4 property to the Google Ads account in the same client's manifest.

    Takes no customer ID on purpose, so it can't link to another client's account.
    Requires the analytics.edit OAuth scope for execute.
    IMPORTANT: Always call with dry_run=true first. Show the user the proposed
    changes and only call with dry_run=false after they explicitly approve.
    """
    req = ToolRequest(businessKey=business_key, dryRun=dry_run, approvalId=approval_id)
    return _run_tool("analytics_link_google_ads", lambda: link_google_ads(req, request_id=None))
```

- [ ] **Step 6: Live dry-run smoke check**

```bash
cd C:/Users/cesar/Code/ads-mcp/servers/analytics && ../../.venv/Scripts/python.exe -c "import sys,json; sys.path.insert(0,'../..'); import mcp_server as m; print(json.dumps(m.analytics_link_google_ads('el-cuis'), indent=1))"
```

Expected: `ok: true`, `mode: dry-run`, `changes[0].after == "2943425139"`.

- [ ] **Step 7: Full suite and commit**

Run: `python -m pytest -q` → all pass.

```bash
git add servers/analytics/tools/write.py servers/analytics/mcp_server.py tests/analytics/test_analytics_write.py
git commit -m "feat(analytics): add link_google_ads write tool

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Site events (`mariachi-cuis`)

**Files** (all paths relative to `C:\Users\cesar\Code\mariachi-cuis`):

- Modify: `src/lib/gtm.ts` (append `trackConversion`)
- Modify: `src/lib/gtm.test.ts` (append tests)
- Modify: `src/components/analytics/track-event.tsx`
- Create: `src/lib/contact-clicks.ts`
- Create: `src/lib/contact-clicks.test.ts`
- Create: `src/components/analytics/click-tracker.tsx`
- Modify: `src/app/[lang]/layout.tsx` (import + mount)

**Interfaces:**

- Consumes: the existing `gtagEvent(name, params)`, `pushDataLayerEvent(event, data?)` and
  `metaTrack(event, opts?) => () => void` from `src/lib/gtm.ts`.
- Produces:
  - `trackConversion({ event, leadSource, metaLead? }): () => void`
  - `contactClickEvent(href): 'phone_click' | 'whatsapp_click' | null`
  - `handleContactClick(event: { target: EventTarget | null }, pathname: string): void`
  - `<ClickTracker />`
- GA4 event names produced: `booking_confirmed`, `estimate_sent`, `contact_form_submit`,
  `phone_click`, `whatsapp_click`. These must match the key event names used in the rollout.

- [ ] **Step 1: Write the failing `trackConversion` tests**

Append inside the existing `describe('analytics helpers', ...)` block in `src/lib/gtm.test.ts`
(before its closing `})`). Also add `trackConversion` to the file's import from `./gtm`.

```ts
  it('sends the named conversion and generate_lead to GA4, and the event to the dataLayer', () => {
    const gtag = vi.fn()
    win.gtag = gtag
    trackConversion({ event: 'booking_confirmed', leadSource: 'booking_deposit' })
    expect(win.dataLayer).toEqual([{ event: 'booking_confirmed' }])
    expect(gtag).toHaveBeenCalledWith('event', 'booking_confirmed', { lead_source: 'booking_deposit' })
    expect(gtag).toHaveBeenCalledWith('event', 'generate_lead', { lead_source: 'booking_deposit' })
    expect(gtag).toHaveBeenCalledTimes(2)
  })

  it('fires Meta Lead only when metaLead is set', () => {
    const fbq = vi.fn()
    win.fbq = fbq
    win.gtag = vi.fn()
    trackConversion({ event: 'contact_form_submit', leadSource: 'contact_form' })
    expect(fbq).not.toHaveBeenCalled()
    trackConversion({ event: 'booking_confirmed', leadSource: 'booking_deposit', metaLead: true })
    expect(fbq).toHaveBeenCalledWith('track', 'Lead')
  })
```

- [ ] **Step 2: Write the failing click tests**

`src/lib/contact-clicks.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { contactClickEvent, handleContactClick } from './contact-clicks'

type FakeEl = {
  getAttribute: (name: string) => string | null
  closest: (selector: string) => FakeEl | null
}

function anchor(href: string, location?: string): FakeEl {
  const section: FakeEl | null = location
    ? { getAttribute: (n) => (n === 'data-track-location' ? location : null), closest: () => null }
    : null
  const a: FakeEl = {
    getAttribute: (n) => (n === 'href' ? href : null),
    closest: (sel) => (sel === 'a[href]' ? a : sel === '[data-track-location]' ? section : null),
  }
  return a
}

function childOf(parent: FakeEl): FakeEl {
  return { getAttribute: () => null, closest: (sel) => (sel === 'a[href]' ? parent : null) }
}

const click = (target: FakeEl | null) => ({ target: target as unknown as EventTarget | null })

describe('contactClickEvent', () => {
  it.each([
    ['tel:+16269220091', 'phone_click'],
    [' TEL:+16269220091', 'phone_click'],
    ['https://wa.me/16269220091', 'whatsapp_click'],
    ['https://wa.me/16269220091?text=Hola', 'whatsapp_click'],
    ['https://www.wa.me/16269220091', 'whatsapp_click'],
    ['https://example.com/?ref=https://wa.me/1', null],
    ['/book', null],
    ['mailto:hola@mariachielcuis.com', null],
    ['', null],
    [null, null],
  ])('%s -> %s', (href, expected) => {
    expect(contactClickEvent(href)).toBe(expected)
  })
})

describe('handleContactClick', () => {
  let gtag: ReturnType<typeof vi.fn>

  beforeEach(() => {
    gtag = vi.fn()
    vi.stubGlobal('window', { gtag, dataLayer: [] })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('sends phone_click with the page path when no location is tagged', () => {
    handleContactClick(click(anchor('tel:+16269220091')), '/en/book')
    expect(gtag).toHaveBeenCalledWith('event', 'phone_click', { link_location: '/en/book' })
  })

  it('uses the nearest data-track-location when present', () => {
    handleContactClick(click(anchor('https://wa.me/16269220091', 'hero')), '/')
    expect(gtag).toHaveBeenCalledWith('event', 'whatsapp_click', { link_location: 'hero' })
  })

  it('handles taps on an icon inside the link', () => {
    handleContactClick(click(childOf(anchor('tel:+16269220091'))), '/')
    expect(gtag).toHaveBeenCalledWith('event', 'phone_click', { link_location: '/' })
  })

  it('ignores other links, non-link targets and null targets', () => {
    handleContactClick(click(anchor('/book')), '/')
    handleContactClick(click({ getAttribute: () => null, closest: () => null }), '/')
    handleContactClick(click(null), '/')
    handleContactClick({ target: {} as EventTarget }, '/')
    expect(gtag).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd C:/Users/cesar/Code/mariachi-cuis && pnpm test -- src/lib/gtm.test.ts src/lib/contact-clicks.test.ts`
Expected: FAIL. `trackConversion` isn't exported, and `./contact-clicks` can't be resolved.

- [ ] **Step 4: Add `trackConversion` to `src/lib/gtm.ts`**

Append:

```ts
// Fires one success page's conversion tracking:
// - `event` onto the dataLayer, where GTM's Meta tags listen.
// - `event` to GA4 by name. Google Ads imports GA4 key events by name, so each
//   lead type needs its own event to carry its own value.
// - GA4 `generate_lead` with `lead_source`, kept for GA4 reporting. Not a key
//   event, so it isn't imported and doesn't double-count.
// - Meta `Lead` when `metaLead` is set (see TrackEvent).
// Returns a cleanup function for the Meta polling.
export function trackConversion({
  event,
  leadSource,
  metaLead = false,
}: {
  event: string
  leadSource: string
  metaLead?: boolean
}): () => void {
  pushDataLayerEvent(event)
  gtagEvent(event, { lead_source: leadSource })
  gtagEvent('generate_lead', { lead_source: leadSource })
  return metaLead ? metaTrack('Lead') : () => {}
}
```

- [ ] **Step 5: Create `src/lib/contact-clicks.ts`**

```ts
import { gtagEvent } from '@/lib/gtm'

export type ContactClickEvent = 'phone_click' | 'whatsapp_click'

const WHATSAPP_LINK = /^https?:\/\/(www\.)?wa\.me\//

// Maps a link's href to the GA4 event a tap on it should send, or null.
export function contactClickEvent(href: string | null | undefined): ContactClickEvent | null {
  if (!href) return null
  const normalized = href.trim().toLowerCase()
  if (normalized.startsWith('tel:')) return 'phone_click'
  if (WHATSAPP_LINK.test(normalized)) return 'whatsapp_click'
  return null
}

type Closest = { closest?: (selector: string) => Element | null }

// Delegated click handler. The tap target may be an icon or span inside the
// link, so it walks up to the nearest <a href>. `link_location` is the nearest
// data-track-location, or the page path.
export function handleContactClick(event: { target: EventTarget | null }, pathname: string): void {
  const target = event.target as Closest | null
  const link = typeof target?.closest === 'function' ? target.closest('a[href]') : null
  if (!link) return
  const name = contactClickEvent(link.getAttribute('href'))
  if (!name) return
  const location = link.closest('[data-track-location]')?.getAttribute('data-track-location') ?? pathname
  gtagEvent(name, { link_location: location })
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pnpm test -- src/lib/gtm.test.ts src/lib/contact-clicks.test.ts` → all PASS.

- [ ] **Step 7: Use `trackConversion` in `TrackEvent`**

Replace the body of `src/components/analytics/track-event.tsx` with:

```tsx
'use client'

import { useEffect } from 'react'
import { trackConversion } from '@/lib/gtm'

// Fires conversion tracking once when a success page mounts (booking
// confirmed, contact sent, quote sent). Tying it to a page view means it
// doesn't depend on transient form state. See trackConversion for what is sent.
// - `metaLead` fires Meta `Lead` directly. Use it only on pages whose GTM event
//   doesn't already trigger the container's Lead tag, to avoid double counting.
export function TrackEvent({
  event,
  leadSource,
  metaLead = false,
}: {
  event: string
  leadSource: string
  metaLead?: boolean
}) {
  useEffect(() => trackConversion({ event, leadSource, metaLead }), [event, leadSource, metaLead])
  return null
}
```

- [ ] **Step 8: Create `src/components/analytics/click-tracker.tsx` and mount it**

```tsx
'use client'

import { useEffect } from 'react'
import { handleContactClick } from '@/lib/contact-clicks'

// Sends GA4 phone_click / whatsapp_click for every tel: and wa.me link on the
// site through one document-level listener, so individual links need no
// tracking code. Capture phase, so a component that stops propagation can't
// hide the tap.
export function ClickTracker() {
  useEffect(() => {
    const onClick = (event: MouseEvent) => handleContactClick(event, window.location.pathname)
    document.addEventListener('click', onClick, { capture: true })
    return () => document.removeEventListener('click', onClick, { capture: true })
  }, [])
  return null
}
```

In `src/app/[lang]/layout.tsx`:

- Add `import { ClickTracker } from '@/components/analytics/click-tracker'` after the
  `ClarityScript` import.
- Add `<ClickTracker />` on the line after `<MobileTabBar locale={lang} dict={dict} />`.

- [ ] **Step 9: Run all site checks**

Run: `pnpm test && pnpm typecheck && pnpm lint && pnpm build`
Expected: all succeed. Existing e2e specs aren't required. If the user wants them, run
`pnpm test:e2e`.

- [ ] **Step 10: Commit (site repo, `main`)**

```bash
cd C:/Users/cesar/Code/mariachi-cuis
git add src/lib/gtm.ts src/lib/gtm.test.ts src/components/analytics/track-event.tsx src/lib/contact-clicks.ts src/lib/contact-clicks.test.ts src/components/analytics/click-tracker.tsx "src/app/[lang]/layout.tsx"
git commit -m "feat(analytics): send named GA4 conversion and contact-click events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Do not push or deploy. The user deploys, per rollout step 2.

---

## Rollout checklist (main session, with the user; not for subagents)

Every write below is a dry-run first. Show the user the result, get explicit approval, then
execute with the dry-run's `approvalId`.

1. **Deploy the site.** The user pushes and deploys `mariachi-cuis`. After the deploy, open the
   live site in a browser **without** `?internal=1` set. GA4 filters internal traffic, so a
   flagged browser's events are dropped. Tap Phone and WhatsApp, then confirm `phone_click` and
   `whatsapp_click` in GA4 Realtime. Also open GTM Preview (or Meta Pixel Helper) on `/book/success`
   and `/contact/success` and confirm each Meta tag (Purchase on `booking_confirmed`, Lead on
   `contact_form_submit`/`estimate_sent`) fires exactly once; the site now also sends these names
   via `gtag()` onto the shared dataLayer, which GTM may treat as a second custom event. If a tag
   fires twice, add a GTM trigger condition excluding gtag-originated events before trusting Meta
   numbers.
2. **New token.**
   - Prerequisite: Enable the Google Analytics Admin API (`analyticsadmin.googleapis.com`) in GCP
     project 826428110016 — it is currently disabled.
   - The user runs `GOOGLE_CLIENT_ID=… GOOGLE_CLIENT_SECRET=… python scripts/get-refresh-token.py`
     and signs in with the account that has GA4 Editor on property 554624356.
   - The user stores the printed refresh token in El Cuis's analytics secret. Give them the exact
     `scripts/onboard-client.py enable` command for `el-cuis` / `analytics` that updates only
     `refresh_token`.
   - Verify with the tokeninfo check from the brainstorm that the El Cuis analytics token now lists
     `analytics.edit`.
3. **Register and restart.** `claude mcp add analytics …`, if it isn't registered yet, using the
   same `.venv` Python and `servers/analytics/mcp_server.py`. Restart Claude Code.
4. **Auto-tagging:** `google_ads_set_auto_tagging('el-cuis', True)`. (El Cuis auto-tagging is
   already on — expect "No change".)
5. **Link:** `analytics_link_google_ads('el-cuis')`.
6. **Key events:** `analytics_create_key_event` for `booking_confirmed`, `estimate_sent`,
   `contact_form_submit`, `whatsapp_click` and `phone_click` (all `ONCE_PER_SESSION`).
7. **Wait for discovery.** Poll `google_ads_get_conversion_actions('el-cuis')` until the 5 GA4
   conversions appear as `HIDDEN`, which takes up to about 24 h. The names in Ads may be prefixed
   (for example "mariachielcuis.com (web) booking_confirmed"). Use the exact names returned.
8. **Import and configure:** `google_ads_update_conversion_action` for each:
   - `booking_confirmed`: `status=ENABLED, primary_for_goal=True, default_value=50`
   - `estimate_sent`, `contact_form_submit`, `whatsapp_click`: `status=ENABLED, primary_for_goal=True, default_value=5`
   - `phone_click`: `status=ENABLED, primary_for_goal=False`
   - "Calls from ads": `phone_call_duration_seconds=60, default_value=5`
9. **Verify.** Submit a test contact form on the live site. Confirm `contact_form_submit` in GA4
   Realtime, and later as a conversion in Google Ads. Record the final state in
   `docs/EL_CUIS_ONBOARDING_DRAFT.md` (Google Ads row: enabled and ready, conversion table).
