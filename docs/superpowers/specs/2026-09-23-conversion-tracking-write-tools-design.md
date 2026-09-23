# Conversion Tracking Write Tools — Design

Date: 2026-09-23
Status: Approved in chat, pending spec review
First client: Mariachi El Cuis (`el-cuis`, Google Ads 2943425139, GA4 property 554624356)

## Goal

Set up El Cuis conversion tracking (GA4 key events → Google Ads conversions) through MCP tools
instead of clicking through the GA4 / Google Ads UIs, and leave behind tools that are reusable for
future clients.

Success means:

- The site sends one distinctly named GA4 event per lead type.
- Those events are GA4 key events, counted once per session.
- GA4 is linked to Google Ads 2943425139 and auto-tagging is on.
- The imported conversions are enabled in Google Ads with the agreed primary/secondary goal and
  values.

## Constraints

- Every write tool supports dry-run and execute modes. Execute requires an `approval_id`
  (CLAUDE.md implementation rules, `docs/INTEGRATION_CONTRACT.md`).
- Every write tool runs business-rule checks before executing.
- No credentials in code. The new OAuth scope is stored only in El Cuis's analytics secret.
- No campaign, budget, keyword or geo changes are part of this work.

## Background findings (2026-09-23 audit)

- The site loads GA4 `G-YGK2HZEWXD` through gtag.js, plus GTM `GTM-W4RLHJDR`, which carries only
  Meta tags. There is no Google Ads tag.
- GA4 has **no key events**. There is no GA4 ↔ Ads link.
- Google Ads has two conversion actions:
  - "Calls from ads" (AD_CALL): primary.
  - "Clicks to call" (GOOGLE_HOSTED): secondary.
- `mariachi-cuis/src/components/analytics/track-event.tsx` already fires on the success pages:
  - `/book/success`: `booking_confirmed`. The Stripe deposit is paid, so this is a real booking.
  - `/book/quote-sent`: `estimate_sent`.
  - `/contact/success`: `contact_form_submit`.

  Those names go only to the GTM dataLayer. GA4 receives a single `generate_lead` event with a
  `lead_source` parameter (added 2026-09-23, so it has no data yet).
- Google Ads imports GA4 key events **by event name** and ignores parameters. A single
  `generate_lead` event therefore cannot carry different values per lead type.
- Taps on `tel:` links are not tracked. WhatsApp (`wa.me`) taps show up only as generic
  enhanced-measurement `click` events.
- The current OAuth token has `adwords` and `analytics.readonly`. It does not have `analytics.edit`.
- The Google Ads API cannot create GA4-imported conversion actions. Once GA4 is linked, Ads
  discovers key events as `HIDDEN` conversion actions. The API can then set `status`
  (HIDDEN → ENABLED), `primary_for_goal`, `category`, `name` and `value_settings`. Other mutations
  return `MUTATE_NOT_ALLOWED`.
- The counting method for GA4-imported conversions comes from the GA4 key event's
  `counting_method`.

## Conversion value model

The value of a lead is profit per booking × the rate at which leads become bookings.

- Profit per booking: $50. The rest of the fee is paid to the musicians.
- Lead-to-booking rate: 1 in 10, from the user's estimate. That makes a lead worth $5.

| GA4 event → Ads conversion | Goal | Value | GA4 counting |
|---|---|---|---|
| `booking_confirmed` | Primary | $50 | Once per session |
| `estimate_sent` | Primary | $5 | Once per session |
| `contact_form_submit` | Primary | $5 | Once per session |
| `whatsapp_click` | Primary | $5 | Once per session |
| `phone_click` | Secondary | — | Once per session |
| Calls from ads (existing) | Primary, minimum call length 60 s | $5 | n/a |
| Clicks to call (existing) | Secondary (unchanged) | — | n/a |
| `generate_lead` (existing) | Not a key event, not imported | — | n/a |

`phone_click` is secondary because "Calls from ads" already counts calls placed from the ads, so
making it primary would double-count. Values only affect bidding under value-based strategies
(Maximize conversion value / tROAS). Under Maximize conversions they serve as reporting. Revisit
the values once real lead and booking counts exist.

## Part 1 — Website (`C:\Users\cesar\Code\mariachi-cuis`)

1. **`TrackEvent`** (`src/components/analytics/track-event.tsx`): in addition to its current
   behavior, call `gtagEvent(event, { lead_source: leadSource })` so GA4 receives
   `booking_confirmed`, `estimate_sent` and `contact_form_submit` by name. Leave the existing
   `pushDataLayerEvent`, `generate_lead` and `metaTrack` calls unchanged, so Meta tracking is not
   affected.
2. **`ClickTracker`**: a new client component mounted once in `src/app/[lang]/layout.tsx`.
   - It registers one delegated `click` listener on `document`, looks for the closest `<a>`, and
     sends:
     - `href` starting with `tel:` → `gtagEvent('phone_click', { link_location })`
     - `href` containing `wa.me` → `gtagEvent('whatsapp_click', { link_location })`
   - `link_location` is the nearest `data-track-location` attribute if one is present, otherwise
     the page path.
   - It removes the listener on unmount.
   - The existing phone and WhatsApp links need no edits.
3. **Tests**: Vitest, in the style of `src/lib/gtm.test.ts`.
   - `TrackEvent` sends both the named event and `generate_lead`.
   - `ClickTracker` fires the right event for `tel:` and `wa.me` links, ignores other links, and
     handles clicks on child elements inside the anchor.

## Part 2 — MCP tools (`C:\Users\cesar\Code\ads-mcp`)

### Shared behavior

All four tools follow the existing write pattern in `servers/google-ads/tools/write.py`:

- `dry_run=True` by default. The dry-run returns a before/after `changes` list built with
  `build_change`, `requires_confirmation=True` and an `approvalId`.
- Execute without an `approval_id` → `BUSINESS_RULE_BLOCKED`.
- Execute when a rule check has failed → `BUSINESS_RULE_BLOCKED`.
- **Idempotent**: when the target state already holds, the dry-run reports "no change" and
  execute is a no-op that returns `executed=False`.
- Upstream API failures → `AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR")`.

### Analytics server

- **New files:** `servers/analytics/tools/write.py`, which uses `google-analytics-admin`
  (`admin_v1beta`) and is added to `servers/analytics/requirements.txt`.
- **Tool registration:** both tools are registered in `servers/analytics/mcp_server.py`.
- **Config:** loaded through `load_platform_runtime_config(platform="analytics", ...)`.
- **Missing permission:** when a GA4 Admin call is rejected for insufficient scope (403),
  `_build_admin_client` raises `AdsMcpError(error_code="AUTH_SCOPE_MISSING")`. Its message tells
  the user to re-run `scripts/get-refresh-token.py` with `analytics.edit` and store the token in
  the client's analytics secret.

| Tool | Inputs | Dry-run shows | Execute |
|---|---|---|---|
| `analytics_create_key_event` | `business_key`, `event_name`, `counting_method="ONCE_PER_SESSION"` (or `ONCE_PER_EVENT`) | Existing key events. Flags when `event_name` already exists. | `create_key_event` on `properties/{id}` |
| `analytics_link_google_ads` | `business_key` | Existing Google Ads links on the property | `create_google_ads_link` with the customer ID taken from the **same client's google-ads manifest config** |

`analytics_link_google_ads` deliberately takes no customer ID argument, so a call cannot link GA4
to another client's Ads account. If the client has no google-ads config, it fails with
`REQUEST_INVALID`.

### Google Ads server

The tools are added to `servers/google-ads/tools/write.py` and registered in `mcp_server.py`.
New helpers in `shared/google_ads_client.py`:

- `get_conversion_action_snapshot`
- `mutate_conversion_action`
- `get_customer_settings_snapshot`
- `mutate_customer_auto_tagging`

| Tool | Inputs | Behavior |
|---|---|---|
| `google_ads_update_conversion_action` | `business_key`, `conversion_name`, optional `status` (`ENABLED`/`HIDDEN`), `primary_for_goal`, `default_value`, `phone_call_duration_seconds` | Looks up the action by exact name. It errors if no match exists or if more than one action has that name. It builds a field mask from the supplied fields only. |
| `google_ads_set_auto_tagging` | `business_key`, `enabled` | Reads `customer.auto_tagging_enabled` and mutates it through `CustomerService`. |

Validation in `google_ads_update_conversion_action` returns `REQUEST_INVALID` when:

- no mutable field is supplied;
- `phone_call_duration_seconds` is set on a non-`AD_CALL` action;
- a field other than `status`, `primary_for_goal` or `value_settings` is set on a
  `GOOGLE_ANALYTICS_4_*` action;
- `default_value` is negative.

**Business rules:** both tools call `evaluate_google_ads_mutation_rules` with new change types
`conversion_action` and `account_setting`. These account-level changes touch no campaign, ad group
or keyword, so the existing RnR/GQ protected-campaign rules pass.

**Read tool update:** `google_ads_get_conversion_actions` also returns `primary_for_goal` and
`phone_call_duration_seconds`. This comes together with the fix, already made, from
`conversion_action.type_` to `conversion_action.type`.

### Other in-flight fixes included

These were made during the audit and are uncommitted. They ship with this work:

- `servers/google-ads/tools/read.py`: `conversion_action.type` in the GAQL query.
- `get_change_history` accepts only `LAST_7_DAYS`, `LAST_14_DAYS` and `THIS_MONTH`, because
  `change_event` rejects start dates older than 30 days.
- Corrected change-history summary text.
- `mcp_server.py` date-range description.

### Tests

New `tests/google_ads/` and `tests/analytics/` folders, with the Google clients mocked in the style
of `tests/meta_ads/`. Coverage for each tool:

- The dry-run returns changes and `requires_confirmation`.
- Execute without `approval_id` is blocked.
- The idempotent no-change case.
- The validation errors above.
- The upstream error mapping.
- For the analytics tools, the `AUTH_SCOPE_MISSING` mapping.
- `analytics_link_google_ads` reads the customer ID from the client's own config.

## Part 3 — OAuth and rollout

- `scripts/get-refresh-token.py` adds `https://www.googleapis.com/auth/analytics.edit` to its
  scopes.
- The new refresh token is written **only** to El Cuis's analytics secret. RnR and GQ keep their
  existing read-only tokens. Issuing a new token does not revoke the old ones.
- The authorizing Google account needs Editor (or higher) on GA4 property 554624356 and admin
  access on Ads 2943425139.

### Rollout order

1. Implement and test the MCP tools in `ads-mcp`, including the in-flight fixes.
2. Implement and test the site changes in `mariachi-cuis`. The user deploys.
3. The user runs the updated `get-refresh-token.py` and stores the token in El Cuis's analytics
   secret. Claude supplies the exact command, because the harness blocks Claude from writing
   secrets.
4. Restart Claude Code to load the new tools.
5. Run the tools in this order. Each step is a dry-run, then user approval, then execute:
   1. `google_ads_set_auto_tagging(enabled=true)`
   2. `analytics_link_google_ads`
   3. `analytics_create_key_event` ×5, per the value table.
   4. Wait for Ads to discover the GA4 conversions as `HIDDEN`, which takes up to about 24 h.
      Check with `google_ads_get_conversion_actions`.
   5. `google_ads_update_conversion_action` for each imported event: status, primary/secondary
      goal and value.
   6. `google_ads_update_conversion_action("Calls from ads", phone_call_duration_seconds=60,
      default_value=5)`
6. Verify:
   - Tap phone and WhatsApp and submit the contact form on the live site.
   - Confirm the events in GA4 Realtime.
   - Confirm conversions appear in Google Ads after the import delay.

## Out of scope

- Building the new El Cuis Search campaign (the next step after this work).
- Removing the old paused Performance Max campaign "Mariachi en Los Ángeles". Keep it paused until
  the new campaign is live, and check the $500 promotional credit terms under Billing → Promotions
  first.
- GA4 event-create rules and GTM API tools (approach B, rejected).
- A Google Ads conversion tag, and enhanced conversions.
