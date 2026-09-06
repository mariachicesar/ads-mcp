# Partial Client Onboarding — Design Spec

Date: 2026-09-06
Status: Approved for planning
Owner: cesar

---

## Problem

Onboarding a new client today assumes the client has every platform. Real
clients don't: some have a Google Business Profile but no Google Ads, some have
Search Console but no GA4. The current flow breaks or degrades badly for them:

- `scripts/push-secrets.py` always writes a `/ads-mcp/{businessKey}/google-ads/config`
  secret, even for a client with no Google Ads — creating a broken, empty secret.
- `shared/runtime_config.py` (`load_google_ads_sdk_config` and friends) raises a
  generic `500 INTERNAL_ERROR` when a platform config is missing or incomplete,
  instead of a clear "this client isn't set up for that platform."
- Nothing records **which** platforms a client actually has. There is no single
  source of truth a human or a tool can read.
- There is no "what is configured for this client?" discovery surface.
- There is no onboarding documentation at all.

## Goals

1. A client can be onboarded with **any subset** of the five platforms
   (Google Ads, GA4 / analytics, Search Console, Google Business Profile,
   Meta Ads) and everything behaves cleanly.
2. Two onboarding paths, both converging on the same storage and the same
   writer code:
   - **Path A** — from `ads-mcp` directly, via a CLI script.
   - **Path B** — from the admin dashboard, via signed HTTP endpoints that
     `backend-rc` calls.
3. Clear, copy-paste onboarding docs.
4. No regression for the two existing clients (`rnr-electrician`, `gq-painting`)
   during or after rollout.

## Non-goals

- De-duplicating the shared Google OAuth credentials (`client_id`,
  `client_secret`, `refresh_token`) that every client currently repeats. Out of
  scope; the manifest deliberately does not hold credentials.
- Building the admin dashboard UI itself (lives in `admin-dashboard-rc`).
- Wiring up real Meta Ads credential plumbing. Meta Ads appears in the manifest
  schema and can be flagged enabled/disabled, but validation only checks that a
  secret exists.
- Moving per-client business rules out of `shared/rules.py` (explicitly kept in
  code per the owner's decision).
- A database. Manifests live in AWS Secrets Manager + local files, matching the
  existing credential-storage pattern.

---

## Design

### 1. The client manifest

One manifest per client. It records **which platforms the client has** plus the
non-secret metadata for each platform. Credentials are **not** in the manifest —
they stay in the existing per-platform secrets at
`/ads-mcp/{businessKey}/{platform}/config`.

```json
{
  "businessKey": "rnr-electrician",
  "displayName": "RnR Electrician",
  "tenantId": 12,
  "industry": "Electrician / home services",
  "status": "active",
  "platforms": {
    "google-ads":     { "enabled": true,  "customer_account_id": "1057140994", "manager_account_id": "4746289774" },
    "analytics":      { "enabled": false },
    "search-console": { "enabled": true,  "site_url": "https://www.rnrelectrician.com" },
    "gbp":            { "enabled": false },
    "meta-ads":       { "enabled": false }
  },
  "createdAt": "2026-09-06T00:00:00Z",
  "updatedAt": "2026-09-06T00:00:00Z"
}
```

**Field rules**

- `businessKey` — kebab-case, unique, immutable once created. Matches the key
  used everywhere else in the codebase.
- `displayName`, `industry` — free text; used by error messages, discovery
  output, and content-agent stubs.
- `tenantId` — optional integer; the `backend-rc` tenant this client belongs to.
- `status` — `"active"` | `"paused"` | `"archived"`. Only `"active"` clients are
  returned by default enumeration; loaders still resolve non-active clients so
  in-flight work doesn't break.
- `platforms` — object with exactly the five known keys:
  `google-ads`, `analytics`, `search-console`, `gbp`, `meta-ads`. Every key is
  always present. Each value is `{ "enabled": bool, ...metadata }`.
- Per-platform metadata keys (non-secret):
  - `google-ads`: `customer_account_id`, `manager_account_id`
  - `analytics`: `property_id`
  - `search-console`: `site_url`
  - `gbp`: `gbp_account_id`, `gbp_location_id`
  - `meta-ads`: `ad_account_id`
- `createdAt` / `updatedAt` — ISO 8601 UTC, set by the writer.

**A platform is "usable" iff** `platforms.{p}.enabled == true` **AND** the
credential secret `/ads-mcp/{businessKey}/{p}/config` resolves with all required
keys for that platform (the `required_keys` already declared in
`shared/runtime_config.py`).

### 2. Manifest storage and precedence

Mirror the existing precedence in `shared/runtime_config.py`
(`_load_env_platform_configs` → `get_platform_config`):

1. **Env var** `ADS_MCP_CLIENT_MANIFESTS_JSON` — a JSON object keyed by
   `businessKey`. Dev / CI override.
2. **Local file** `clients/{businessKey}.json` — committed, dev-facing. Safe to
   commit because it holds no secrets. Used when the env var is absent or lacks
   the key.
3. **AWS secret** `/ads-mcp/{businessKey}/manifest` — production. Written by both
   onboarding paths.

First hit wins; no merging across layers.

**Client index.** AWS Secrets Manager cannot cheaply wildcard-list secrets, so
enumeration uses an index:

- **AWS secret** `/ads-mcp/_index/clients` — a JSON array of business keys,
  e.g. `["rnr-electrician", "gq-painting"]`. Updated (add / remove key) by every
  manifest write and delete.
- In dev, enumeration also globs `clients/*.json` and reads env-var keys, then
  unions all three sources.

### 3. New module: `shared/manifest.py`

Pure functions, no framework imports, unit-testable in isolation.

```python
def load_client_manifest(business_key: str) -> ClientManifest | None
    # env -> local file -> secret. None if not found anywhere.

def list_client_manifests(*, include_inactive: bool = False) -> list[ClientManifest]
    # union of index secret + clients/*.json + env keys; deduped by businessKey.

def client_readiness(business_key: str) -> ReadinessReport
    # per-platform {enabled, ready, missingKeys}. Resolves each credential
    # secret and checks required_keys.

def platform_enabled(manifest: ClientManifest, platform: str) -> bool

def upsert_client_manifest(manifest: ClientManifest, *, dry_run: bool = False) -> ClientManifest
    # writes /ads-mcp/{key}/manifest, updates _index, stamps updatedAt/createdAt.
    # dry_run returns the would-be manifest without writing.

def set_platform_enabled(business_key: str, platform: str, *, enabled: bool,
                         metadata: dict | None = None, dry_run: bool = False) -> ClientManifest

def delete_client_manifest(business_key: str, *, dry_run: bool = False) -> None
    # removes manifest secret + index entry. Does NOT delete credential secrets
    # (left for a deliberate separate cleanup).
```

`REQUIRED_KEYS_BY_PLATFORM` — a single mapping reused by both the readiness
check and the platform loaders, replacing the per-function `required_keys`
tuples currently scattered in `runtime_config.py` (those functions call into
this mapping).

### 4. Models (added to `shared/models.py`)

```python
class PlatformConfig(BaseModel):
    enabled: bool = False
    # platform-specific metadata is permitted as extra keys
    model_config = ConfigDict(extra="allow")

class ClientManifest(BaseModel):
    businessKey: str
    displayName: str
    tenantId: int | None = None
    industry: str | None = None
    status: Literal["active", "paused", "archived"] = "active"
    platforms: dict[str, PlatformConfig]
    createdAt: str | None = None
    updatedAt: str | None = None

class PlatformReadiness(BaseModel):
    enabled: bool
    ready: bool
    missingKeys: list[str] = []

class ReadinessReport(BaseModel):
    businessKey: str
    displayName: str
    status: str
    platforms: dict[str, PlatformReadiness]
```

Manifest validation normalizes `platforms` so all five known keys are always
present (missing ones default to `{"enabled": false}`).

### 5. Config loading becomes partial-aware (`shared/runtime_config.py`)

`load_platform_runtime_config` gains a manifest pre-check:

| Situation | Today | New behavior |
|---|---|---|
| No manifest found anywhere for `businessKey` | works off secret/env only | **unchanged** — fall through to current behavior (pre-migration safety) |
| Manifest exists, `platforms.{p}.enabled` is false/absent | `500 INTERNAL_ERROR` (or `400` "no config") | **`404 PLATFORM_NOT_CONFIGURED`** — `"{displayName} is not set up for {platform}. Onboard it with scripts/onboard-client.py or the admin dashboard."` |
| Manifest exists, platform enabled, credential secret missing or missing required keys | `500 INTERNAL_ERROR` | **`409 PLATFORM_CONFIG_INCOMPLETE`** with `details.missingKeys` |
| Manifest exists, platform enabled, secret complete | works | works; platform metadata (`customer_account_id`, `site_url`, …) is merged from the manifest, with the credential secret as fallback for those keys |

**New error codes** (string values only; `shared/errors.py` needs no structural
change): `PLATFORM_NOT_CONFIGURED`, `PLATFORM_CONFIG_INCOMPLETE`.

**Merge order** for the dict returned to callers: credential secret first, then
manifest platform metadata overlaid on top (manifest wins for the non-secret
IDs; secret still supplies anything the manifest omits, preserving today's
secrets-only clients).

`load_google_ads_config`, `load_google_ads_sdk_config`, and the analytics /
search-console / gbp loaders keep their signatures; they pass a `platform` name
through to the shared pre-check.

### 6. Path A — onboarding from `ads-mcp`: `scripts/onboard-client.py`

A single script, interactive prompts **and** flag-driven, with subcommands:

| Subcommand | Behavior |
|---|---|
| `add --key K --name N [--tenant T] [--industry I]` | Create a new client. Prompts (or flags) for which of the five platforms are enabled; for each enabled platform, prompts for its metadata IDs and credential values. |
| `update --key K` | Same prompts, pre-filled from the current manifest. |
| `show --key K` | Print the manifest + readiness report (credentials redacted). |
| `list` | Print all clients + per-platform readiness. |
| `enable --key K --platform P` / `disable ...` | Flip one platform; `enable` prompts for missing metadata/creds. |
| `validate --key K [--platform P]` | Run one cheap live API call per enabled platform; report pass/fail. |

**Common flags**

- `--dry-run` — print the manifest and every secret path that would be written,
  write nothing (same style as `push-secrets.py`).
- `--from-file PATH` — read the existing flat `local-dev-config.json` format and
  derive the manifest + secrets from which fields are present (back-compat).
- `--region` — AWS region (default `us-east-1`, matching `push-secrets.py`).

**Writes performed by `add` / `update`:**

1. `/ads-mcp/{key}/manifest` (via `upsert_client_manifest`)
2. `/ads-mcp/{key}/{platform}/config` for each enabled platform (credentials)
3. `/ads-mcp/_index/clients` (add the key)

**Validation** (`validate`, and automatically at the end of `add`/`update`
unless `--skip-validate`): one lightweight call per enabled platform —
`list_accounts` (Google Ads), a 1-row report (GA4), `sites.list` (Search
Console), locations list (GBP), account fetch (Meta). Failures are reported, not
fatal; the manifest is still written so the operator can fix creds and re-run
`validate`.

**Post-onboarding output** — the script prints a checklist:

- `shared/rules.py` snippet to paste if the client needs protected campaigns /
  geo locks / approval gates (template included in output).
- `servers/content-agent/brands/{key}.md` — create from the template if the
  content agent will be used for this client.
- `CLAUDE.md` — add a business section.
- `bash scripts/deploy.sh` to ship.

**`scripts/push-secrets.py` is refactored** to import and call the same core
helpers in `scripts/_onboard_core.py` (extracted module). Result: it stops
force-writing an empty `google-ads` secret — it now writes a platform secret
only when that platform's fields are present in the config file, and it writes a
manifest derived from the same presence checks. Its CLI and existing behavior
for fully-configured clients are unchanged.

### 7. Path B — onboarding from the admin dashboard: signed HTTP endpoints

New router `servers/orchestrator/admin.py`, mounted in
`servers/orchestrator/main.py` under `/admin/`. The orchestrator is chosen
because it is the cross-agent coordination service and already the dashboard's
entry point.

| Method + path | Purpose | Body / notes |
|---|---|---|
| `POST /admin/clients` | Upsert a manifest | `{ businessKey, displayName, tenantId?, industry?, status?, platforms }`. Supports `{ "dryRun": true }`. Returns stored manifest + readiness report. |
| `GET /admin/clients` | List all manifests | Query `?includeInactive=true` optional. **Never returns credentials.** |
| `GET /admin/clients/{businessKey}` | One manifest + readiness | 404 if unknown. |
| `PUT /admin/clients/{businessKey}/platforms/{platform}` | Enable/disable + set metadata + credentials | `{ enabled, metadata?: {...}, credentials?: {...}, dryRun? }`. Credentials written to `/ads-mcp/{key}/{platform}/config`, **never echoed back**. |
| `POST /admin/clients/{businessKey}/platforms/{platform}/validate` | Live check | Returns `{ ok, detail }`. |
| `DELETE /admin/clients/{businessKey}/platforms/{platform}` | Disable a platform | Sets `enabled: false`; does not delete the credential secret. |

**Auth.** `shared/auth.py` `SignedRequestMiddleware.dispatch` currently gates
only paths starting with `/tools/`. Extend the prefix check to also cover
`/admin/`. Same HMAC-SHA256 canonical-request scheme, same `backend-rc` signing
key, same nonce/timestamp handling. No new auth code — one condition change plus
a test.

**Responses** reuse the existing envelope conventions (`ok`, `service`,
`requestId`, `errorCode`/`message` on failure). Manifest write responses include
`mode: "dry-run" | "execute"` for parity with the write contract.

**Redaction.** A single `manifest.to_public_dict()` / readiness serializer is the
only thing the admin routes are allowed to return. Credential values never enter
a response body. Enforced by a test that scans every admin response for known
secret-key names.

### 8. Discovery tool for Claude (MCP)

Add to `servers/orchestrator/mcp_server.py`:

- `list_clients()` → array of readiness reports (all active clients).
- `get_client_status(business_key)` → one readiness report + manifest metadata.

Both read manifests only; no credentials. Lets Claude Desktop answer "which
clients do we have and what's set up for each?"

### 9. Business rules — unchanged

`shared/rules.py` keeps `GOOGLE_ADS_RULES` as a hardcoded dict.
`GOOGLE_ADS_RULES.get(business_key, {})` already returns `{}` for an unknown key,
so a newly onboarded client simply has no extra rules — the safe default. The
onboarding script's output and `docs/ONBOARDING.md` include a paste-ready
`rules.py` template. No code change in this file beyond an explanatory comment.

### 10. Content agent — minimal touch

`servers/content-agent/mcp_server.py` already degrades gracefully when
`brands/{businessKey}.md` is absent (`brandContextLoaded: false`). Change:

- Add `servers/content-agent/brands/_TEMPLATE.md`.
- When a brand file is missing but a manifest exists, include
  `warnings: ["No brand file for {key}; using generic voice. Create brands/{key}.md from _TEMPLATE.md."]`
  in the response.

No hard error, no generation-on-the-fly.

### 11. Migration

1. Commit `clients/rnr-electrician.json` and `clients/gq-painting.json` — full
   manifests, metadata only, derived from `CLAUDE.md` + `local-dev-config.example.jsonc`.
   - `rnr-electrician`: google-ads ✔, search-console ✔, analytics ✘ (no
     `ga4_property_id` in example), gbp ✘, meta-ads ✘.
   - `gq-painting`: google-ads ✔, search-console ✔, analytics ✘, gbp ✘, meta-ads ✘.
   - (Owner confirms the real enabled set at implementation time; the committed
     files are the source of truth for dev.)
2. `scripts/backfill-manifests.py` — for each key in `local-dev-config.json`,
   read which platform secrets already exist in AWS, build a manifest, write
   `/ads-mcp/{key}/manifest`, and populate `/ads-mcp/_index/clients`.
   `--dry-run` supported.
3. Deploy order is irrelevant: the loader falls back to current behavior for any
   `businessKey` without a manifest, so a half-migrated fleet still works.

### 12. Testing

No test suite exists today. This work introduces `pytest` + a `tests/`
directory.

| Area | Tests |
|---|---|
| `shared/manifest.py` | precedence (env > file > secret); `list_client_manifests` union + dedupe; `client_readiness` for each combination of enabled/secret-present/keys-missing; `upsert` stamps timestamps + updates index; `--dry-run` writes nothing |
| `shared/runtime_config.py` | no-manifest fallback unchanged; `PLATFORM_NOT_CONFIGURED` on disabled; `PLATFORM_CONFIG_INCOMPLETE` with `missingKeys`; manifest metadata merge with secret fallback |
| `servers/orchestrator/admin.py` | signature required (401 without); `dryRun` writes nothing; credential redaction scan on every response; upsert + readiness round-trip; unknown key → 404 |
| `shared/auth.py` | `/admin/` path now gated by signing when `require_signed_requests=true` |
| `scripts/_onboard_core.py` | flat-config → manifest derivation; google-ads secret NOT written when fields absent |
| smoke | extend `scripts/signed_request_smoke.py` with a signed `POST /admin/clients` dry-run call |

Fakes: an in-memory secrets backend (dict) injected into `shared/secrets.py`
via a test fixture, so no AWS calls in unit tests.

### 13. Docs

| File | Content |
|---|---|
| **`docs/ONBOARDING.md`** (new, primary) | Opening line: "A client can have any subset of the five platforms." Platform table: for each platform — what it needs (IDs, OAuth scopes), where to find each value, whether it's read-only or write-capable. **Path A**: copy-paste `onboard-client.py` commands, dry-run first, expected output, validation, post-onboarding checklist. **Path B**: the `/admin/clients` request/response examples for the dashboard team. **Troubleshooting**: `PLATFORM_NOT_CONFIGURED`, `PLATFORM_CONFIG_INCOMPLETE`, validation failures, index out of sync. |
| **`docs/CLIENT_MANIFEST.md`** (new) | Manifest JSON schema, every field, the five platform metadata shapes, storage precedence, the `_index/clients` secret, redaction rules. |
| `docs/INTEGRATION_CONTRACT.md` | Add the `/admin/clients` endpoints to "Minimum API shape between backend-rc and ads-mcp"; note manifest ownership sits with `ads-mcp`. |
| `README.md` | Replace the "Create your local credentials file" section with a short pointer to `docs/ONBOARDING.md`; note `push-secrets.py` is now partial-friendly and `clients/*.json` exist. |
| `CLAUDE.md` | Add Build Order item 13 "Client onboarding (partial-platform)"; add a Standing Rule that the manifest is the source of truth for which platforms a client has, and that disabled platforms must return `PLATFORM_NOT_CONFIGURED`. |

---

## Interfaces summary (what other components depend on)

- **`backend-rc`** gains six `/admin/*` endpoints on the orchestrator, same
  signing scheme it already uses for `/tools/*`. Nothing existing changes.
- **Claude Desktop** gains `list_clients` / `get_client_status` MCP tools.
- **Operators** gain `scripts/onboard-client.py`; `scripts/push-secrets.py`
  keeps working.
- **Existing tool endpoints** are unchanged on the happy path; on the failure
  path they now return `404 PLATFORM_NOT_CONFIGURED` / `409
  PLATFORM_CONFIG_INCOMPLETE` instead of `500 INTERNAL_ERROR` for clients that
  have a manifest.

## Rollout order

1. `shared/models.py` + `shared/manifest.py` + tests (no behavior change yet).
2. `shared/runtime_config.py` manifest pre-check + tests (fallback keeps old
   behavior).
3. `scripts/_onboard_core.py` + refactor `push-secrets.py` + `onboard-client.py`.
4. `scripts/backfill-manifests.py`; commit `clients/*.json`; run backfill.
5. `servers/orchestrator/admin.py` + auth prefix change + MCP discovery tools.
6. Docs.

## Open questions for the owner (resolve during planning)

- Confirm the real enabled-platform set for `rnr-electrician` and `gq-painting`
  (the example config only proves google-ads + search-console).
- Region: `push-secrets.py` hardcodes `us-east-1` but `shared/secrets.py` uses
  `AWS_REGION`. Standardize on `AWS_REGION` with an `us-east-1` default?
