# Meta Ads Integration Plan

## Goal

Replace the `servers/meta-ads/` stub with a real Meta Marketing API integration covering both Facebook and Instagram placements, following the exact architectural shape already used by `servers/google-ads/`: a `shared/meta_ads_client.py` SDK wrapper, a `tools/read.py` + `tools/write.py` package, HMAC-signed FastAPI routes in `main.py`, and a FastMCP stdio layer in `mcp_server.py`. Every write tool follows the `dry-run -> approval -> execute` contract defined in `docs/INTEGRATION_CONTRACT.md`.

**Current state:**
- `servers/meta-ads/mcp_server.py` has one tool, `meta_ads_get_campaign_performance`, returning hardcoded placeholder data.
- `servers/meta-ads/main.py` has one route, `/tools/get_campaign_performance`, same placeholder response.
- `facebook-business>=22.0.0` (Meta's official Python SDK) is in `requirements.txt` but never imported.
- Both `clients/rnr-electrician.json` and `clients/gq-painting.json` already have a `meta-ads` platform block, currently `{"enabled": false}`.
- `shared/models.py` already lists `meta-ads` in `KNOWN_PLATFORMS`.
- `shared/manifest.py`'s `REQUIRED_KEYS_BY_PLATFORM["meta-ads"]` already exists as `("ad_account_id", "access_token")` — this plan extends it, it doesn't create it from scratch.
- `servers/orchestrator/tools/workflow_runs.py`'s `SUPPORTED_SERVICES` set already includes `"meta-ads"` — no orchestrator change needed for basic step execution once the real tools exist.

**Instagram is not a separate integration.** The Meta Marketing API returns Facebook and Instagram placement data from the same endpoints via the `publisher_platform` breakdown dimension (`facebook` / `instagram` / `audience_network` / `messenger`). There is no separate "Instagram Ads API" to integrate — this plan treats IG as a reporting/targeting dimension, not a second client.

Cost note: the Marketing API itself is free to call (rate-limited by Meta's tiered access system); the only real dollar cost is ad spend on the account itself, consistent with the "no paid subscriptions" constraint.

---

## `shared/meta_ads_client.py` — proposed structure

Mirrors `shared/google_ads_client.py`: one `build_meta_ads_client(config, *, tool=None)` factory plus one function per snapshot/mutation, each raising `AdsMcpError` on SDK failure so `tools/read.py` / `tools/write.py` stay thin.

```python
# shared/meta_ads_client.py — sketch, not implementation

def build_meta_ads_api(config: dict, *, tool: str | None = None):
    """FacebookAdsApi.init(access_token=..., app_id=..., app_secret=...)
    Raises AdsMcpError(INTERNAL_ERROR) if facebook-business isn't installed
    or required credential keys are missing — same pattern as
    build_google_ads_client's ModuleNotFoundError handling."""

def get_ad_account(config: dict, *, tool=None) -> AdAccount:
    """Wraps AdAccount(f'act_{config['ad_account_id']}')."""

# --- read/snapshot functions ---
def get_campaign_insights_snapshot(config, *, campaign_ids=None, date_preset="last_30d", breakdowns=None, tool=None) -> list[dict]
def get_adset_insights_snapshot(config, *, adset_ids=None, date_preset="last_30d", tool=None) -> list[dict]
def get_ad_insights_snapshot(config, *, ad_ids=None, date_preset="last_30d", breakdowns=None, tool=None) -> list[dict]
def get_publisher_platform_breakdown(config, *, object_id, level, date_preset="last_30d", tool=None) -> list[dict]
def list_ad_creatives(config, *, tool=None) -> list[dict]

# --- lookup helpers (mirror get_campaign_budget_snapshot / get_campaign_status_snapshot) ---
def get_campaign_snapshot(config, *, campaign_name=None, campaign_id=None, tool=None) -> dict
def get_adset_snapshot(config, *, adset_name=None, adset_id=None, tool=None) -> dict

# --- write/mutation functions ---
def create_campaign(config, *, name, objective, status="PAUSED", tool=None) -> dict
def create_adset(config, *, campaign_id, name, daily_budget_micros, targeting, optimization_goal, billing_event, status="PAUSED", tool=None) -> dict
def create_ad(config, *, adset_id, name, creative_id, status="PAUSED", tool=None) -> dict
def mutate_campaign_status(config, *, campaign_id, new_status, tool=None) -> dict
def mutate_adset_status(config, *, adset_id, new_status, tool=None) -> dict
def mutate_ad_status(config, *, ad_id, new_status, tool=None) -> dict
def update_campaign_budget_amount(config, *, campaign_id, new_daily_budget_micros, tool=None) -> dict
def update_adset_budget_amount(config, *, adset_id, new_daily_budget_micros, tool=None) -> dict
```

Key differences from the Google Ads client that the implementer must not paper over:
- **No refresh tokens.** Meta uses long-lived user or System User access tokens (~60 days for user tokens; System User tokens on a Business Manager can be set to never-expire). `access_token` in the credential secret is the whole story — there is no `client_secret` + `refresh_token` exchange step at request time like Google Ads. Token *rotation* is an operational/manual task, not a runtime code path.
- **Money is in whole currency units at the account's currency precision in some endpoints and integer minor-units (cents) in others.** Campaign/ad set `daily_budget` fields are in the account's minor currency unit (e.g. cents for USD), not micros. Do not reuse Google Ads' `* 1_000_000` micros convention — convert explicitly and name fields `daily_budget_cents` / `daily_budget` to avoid a silent unit-mismatch bug.
- **Async job pattern for insights on large accounts.** For small accounts like RnR/GQ, synchronous `AdAccount.get_insights()` calls are fine; the plan does not need the async insights-job/polling pattern Meta recommends for large advertisers.

---

## Credentials: Secrets Manager shape

Follows the existing convention exactly: `/ads-mcp/{business_key}/{platform}/config`, same as Google Ads' `/ads-mcp/{business_key}/google-ads/config`.

```json
// Secret ID: /ads-mcp/rnr-electrician/meta-ads/config
{
  "ad_account_id": "act_XXXXXXXXXXXXX",
  "access_token": "EAAG...",
  "app_id": "1234567890123456",
  "app_secret": "***",
  "page_id": "0987654321",
  "instagram_business_account_id": "1789012345678"
}
```

New keys this plan adds to `REQUIRED_KEYS_BY_PLATFORM["meta-ads"]` in `shared/manifest.py` (currently only `ad_account_id`, `access_token`):
- `app_id`, `app_secret` — required to init `FacebookAdsApi` properly and to debug/inspect token validity via `/debug_token`.
- `page_id` — required for ad creative construction (link ads reference a Page); also the anchor object for organic reach if that's ever added.

Optional, manifest-metadata level (like `manager_account_id` for Google Ads — can live in `clients/*.json` rather than the secret since it's not sensitive):
- `instagram_business_account_id` — needed only if a client wants IG-native ad creative variants or IG Shopping tags; not needed for basic FB+IG placement reporting, since `publisher_platform` breakdown works off the ad account alone.

`clients/rnr-electrician.json` / `clients/gq-painting.json` stay `{"enabled": false}` for `meta-ads` until a client actually decides to run Meta ads — this plan does not flip those flags.

---

## Read tools — build order

All return `build_success_response(..., mode="read", ...)` exactly like `tools/read.py` in google-ads.

1. **`list_ad_accounts`** — mirrors `list_accounts`. Confirms the configured `ad_account_id` is reachable with the stored token before anything else is built; cheapest possible smoke test.
2. **`get_campaign_performance`** — replaces the placeholder. Campaign-level insights: `impressions`, `clicks`, `spend`, `ctr`, `cpc`, `reach`, `frequency`, over a `date_preset` (mirror Google Ads' `dateRange` validation pattern — `LAST_7_DAYS` etc. mapped to Meta's `date_preset` enum: `last_7d`, `last_30d`, `this_month`, `last_month`).
3. **`get_adset_performance`** — same shape, one level down (Meta's hierarchy is Campaign → Ad Set → Ad, vs Google's Campaign → Ad Group → Ad — name the tool `get_adset_performance` not `get_ad_group_performance` to avoid implying a false 1:1 mapping).
4. **`get_ad_performance`** — ad-level insights, mirrors `get_ad_performance` in google-ads.
5. **`get_publisher_platform_breakdown`** — the FB-vs-IG tool. Same insights call as #2–4 with `breakdowns=["publisher_platform"]` added, returning one row per platform per object. This is the tool that actually answers "how is this doing on Instagram vs Facebook" — it is a breakdown parameter, not a separate data source.
6. **`get_audience_performance`** — insights with `breakdowns=["age", "gender"]` (and optionally `["country"]` for geo), mirrors `get_audience_performance` in google-ads conceptually but the underlying Graph API breakdown list is different from Google Ads' segment list.
7. **`list_creative_assets`** — lists `AdCreative` objects on the account (name, thumbnail URL, associated ad IDs, status) so the Content Agent or a human can see what's already built before generating new creative.

## Write tools — build order and dry-run contract

Every write tool follows the same three-part shape as `update_campaign_budget` in `servers/google-ads/tools/write.py`:
1. Validate payload, run `evaluate_meta_ads_mutation_rules(...)` (new function in `shared/rules.py`, see below).
2. Fetch a snapshot of current state, compute the diff as `build_change(...)` entries.
3. If `dryRun is not False`: return `mode="dry-run"`, `requires_confirmation=True`, `executed=False`. If `dryRun is False`: require `approvalId`, re-check rules, block on any failing rule check, then call the mutation function and return `mode="execute"`, `executed=True`.

Build order:

1. **`create_campaign`** — payload: `name`, `objective` (`OUTCOME_TRAFFIC`, `OUTCOME_LEADS`, `OUTCOME_ENGAGEMENT`, etc.), `status` (always creates `PAUSED` regardless of requested value — a new campaign should never go live without a second explicit "turn it on" action; this mirrors how RSA creation in Google Ads sets `ENABLED` only because there's an existing reviewed ad group context, whereas a brand-new campaign has no prior human review). Dry-run has no "before" state (nothing exists yet) — `changes` shows only an `after` value with `status: "proposed"`.
2. **`create_adset`** — payload: `campaign_id`, `name`, `daily_budget` (dollars, converted to minor units in the client), `targeting` (geo + age/gender, matching the business's documented service area from `CLAUDE.md`), `optimization_goal`, `billing_event`. Also created `PAUSED`.
3. **`create_ad`** — payload: `adset_id`, `name`, `creative_id` (must already exist — this plan does not cover creative/asset upload, which belongs to the Content Agent's image/video generation pipeline, a separate piece of work). Also created `PAUSED`.
4. **`set_campaign_status` / `set_adset_status` / `set_ad_status`** — mirrors `set_campaign_status` / `set_ad_group_status` / `update_ad_status` in google-ads exactly, just against `id` instead of `resource_name` (Meta's SDK uses plain numeric IDs, no resource-name strings).
5. **`update_campaign_budget` / `update_adset_budget`** — mirrors `update_campaign_budget` exactly, with the minor-units conversion caveat above.

## Extending business rules to Meta accounts

`shared/rules.py` currently has one dict, `GOOGLE_ADS_RULES`, keyed by `business_key`, and one function, `evaluate_google_ads_mutation_rules`. This plan adds a **parallel** dict and function rather than trying to generalize the two platforms into one shared rule engine prematurely — the mutation-key vocabularies differ (Google's `geoTargets`/`keywords` vs Meta's `targeting`/campaign `objective`), and forcing a shared abstraction now would be speculative given neither RnR nor GQ has Meta enabled yet:

```python
# shared/rules.py — additions

META_ADS_RULES: dict[str, dict[str, Any]] = {
    # Populated once a client actually enables meta-ads and CLAUDE.md
    # documents their Meta-specific protected rules — e.g.:
    # "rnr-electrician": {
    #     "max_daily_budget_cents": 1500,   # mirror the $15/day Google Ads cap
    #     "lock_geo_changes": True,          # same USC Village move lock
    # },
}

def evaluate_meta_ads_mutation_rules(
    *, business_key: str, payload: dict[str, Any] | None = None,
    campaign_id: str | None = None, change_type: str | None = None,
) -> list[dict[str, Any]]:
    ...  # same build_rule_check() shape as evaluate_google_ads_mutation_rules
```

When a client's `CLAUDE.md` block later documents Meta-specific protected campaigns/budgets, add an entry to `META_ADS_RULES` the same way `GOOGLE_ADS_RULES["rnr-electrician"]` was added — this is a data change, not a code change, once the function exists.

---

## Prior art: what to take and what to leave

Both `github.com/oliverames/meta-mcp-server` and `github.com/mikusnuz/meta-ads-mcp` are useful for **Graph API call shapes** — exact field names for insights (`spend`, `actions`, `cost_per_action_type`), the SDK's cursor-pagination pattern for `get_insights()`, and how they structure `targeting` spec dicts for ad set creation. Read them for that.

Do **not** copy their execution model directly:
- Both call Meta's write endpoints immediately on tool invocation with no confirmation gate — that violates this repo's "every write tool must support dry-run before execute" rule (`CLAUDE.md` Implementation Rules) and the `docs/INTEGRATION_CONTRACT.md` write flow (dry-run → approval → execute).
- Neither has a business-rule layer — budget caps and protected-campaign locks must be added here, they don't exist upstream.
- Neither uses HMAC-signed server-to-server requests — they assume a trusted local MCP client. This repo's `SignedRequestMiddleware` (`shared/auth.py`) must wrap every route exactly like it already does for `meta-ads` and `google-ads`.
- Credential handling in both projects assumes a `.env` file or CLI flag; this repo requires AWS Secrets Manager per `CLAUDE.md`'s "No credentials in code or committed environment files."

Net: use them as a Graph API field/parameter reference, not as an architecture to port.

---

## File layout

- `shared/meta_ads_client.py` — new, mirrors `shared/google_ads_client.py`.
- `servers/meta-ads/tools/read.py` — new.
- `servers/meta-ads/tools/write.py` — new.
- `servers/meta-ads/main.py` — modify: replace the one placeholder route with routes for every tool above, matching the `servers/google-ads/main.py` pattern of one thin FastAPI wrapper per tool function.
- `servers/meta-ads/mcp_server.py` — modify: replace the one placeholder `@mcp.tool()` with one per read/write tool, matching `servers/google-ads/mcp_server.py`'s pattern (not shown above but same shape as `meta_ads_get_campaign_performance` today, minus the placeholder note).
- `shared/manifest.py` — modify: extend `REQUIRED_KEYS_BY_PLATFORM["meta-ads"]`.
- `shared/rules.py` — modify: add `META_ADS_RULES` + `evaluate_meta_ads_mutation_rules`.
- `servers/meta-ads/requirements.txt` — no change needed, `facebook-business` is already listed.

---

## Open questions (need answers before implementation starts)

1. **Does either client actually want to run Meta ads yet?** Both manifests have `meta-ads: {enabled: false}`. Confirm business intent before building write tools that will sit unused — read-only reporting tools (items 1–7 above) are low-risk to build speculatively since they can't touch anything, but write tools should probably wait for an actual "yes, run this on Facebook/Instagram" decision.
2. **Meta Business Manager access**: is there an existing Business Manager account for RnR and/or GQ, or does one need to be created? Ad accounts, Pages, and System Users all live under a Business Manager.
3. **Credentials to create**: a Meta App (in developers.facebook.com) to get `app_id`/`app_secret`, then either (a) a long-lived User Access Token via the OAuth token exchange, or (b) a System User + System User Access Token scoped to the ad account (recommended — doesn't expire on a fixed schedule tied to a human's login session). Marketing API access currently requires the app to pass Meta's App Review for `ads_management` permission beyond a small testing tier — confirm whether the existing dev-mode/test-tier access (usually capped at a handful of ad accounts added as testers) is sufficient for RnR + GQ before assuming App Review is needed.
4. **Which Facebook Page(s)** are linked to each business, for `page_id`, and whether each business has a connected Instagram professional/business account for `instagram_business_account_id`.
5. **Budget/geo rules for Meta specifically** — once a client is ready to launch, what should `META_ADS_RULES` contain? (Likely mirrors the existing Google Ads caps in `CLAUDE.md`, but confirm rather than assume a 1:1 copy — Meta CPMs and audience sizes behave differently than Google Search CPCs.)

---

## Suggested build order (this doc only)

1. `shared/meta_ads_client.py` with just `build_meta_ads_api` + `get_ad_account` + `list_ad_accounts` — smallest possible slice that proves the credential shape and SDK wiring work end to end.
2. `list_ad_accounts` read tool (server route + MCP tool), tested against a real (even zero-spend) ad account.
3. `get_campaign_performance`, `get_adset_performance`, `get_ad_performance` — the three insight levels, since they share almost all of their query-building code.
4. `get_publisher_platform_breakdown` and `get_audience_performance` — same insights call, different `breakdowns` param, so cheap to add once #3 exists.
5. `list_creative_assets`.
6. `shared/rules.py` additions (`META_ADS_RULES`, `evaluate_meta_ads_mutation_rules`) — build this before any write tool, not after, so every write tool from the start runs through rule checks.
7. Write tools in the order listed above: `create_campaign` → `create_adset` → `create_ad` → status-toggle tools → budget-update tools.

See the cross-document build order in `docs/YOUTUBE_VIDEO_CAMPAIGNS_PLAN.md` for how this plan sequences against the other two.
