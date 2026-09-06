# Partial Client Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a client be onboarded with any subset of the five marketing platforms (Google Ads, GA4, Search Console, Google Business Profile, Meta Ads), from either a CLI script or signed admin HTTP endpoints, with clear errors and docs.

**Architecture:** A per-client *manifest* (JSON, no credentials) records which platforms a client has plus non-secret metadata. It is resolved env → `clients/*.json` → AWS secret `/ads-mcp/{key}/manifest`, with an index secret `/ads-mcp/_index/clients` for enumeration. `shared/runtime_config.py` consults the manifest before loading any platform config and returns `404 PLATFORM_NOT_CONFIGURED` / `409 PLATFORM_CONFIG_INCOMPLETE` instead of a generic 500. Onboarding logic lives in one core module used by both `scripts/onboard-client.py` and a new signed `/admin/clients*` router on the orchestrator service.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI + Starlette, boto3 (AWS Secrets Manager), FastMCP, pytest (new).

**Spec:** `docs/superpowers/specs/2026-09-06-partial-client-onboarding-design.md`

## Global Constraints

- Python 3.12; type hints with `from __future__ import annotations` at top of every new module.
- No credentials in code or committed files. `clients/*.json` manifests hold **metadata only** (account IDs, property IDs, site URLs) — never `client_secret`, `refresh_token`, `developer_token`, `access_token`.
- Every write tool must support a dry-run mode and an execute mode.
- Business rules from `CLAUDE.md` must be enforced by tools before execution — unchanged here; `shared/rules.py` stays a hardcoded dict.
- AWS region: resolve from `AWS_REGION` env with default `"us-east-1"` (matches `shared/config.py` `aws_region` + `scripts/push-secrets.py` `REGION`).
- The five known platform keys, in this exact order and spelling: `google-ads`, `analytics`, `search-console`, `gbp`, `meta-ads`.
- Manifest secret id: `/ads-mcp/{businessKey}/manifest`. Index secret id: `/ads-mcp/_index/clients`. Credential secret id (unchanged): `/ads-mcp/{businessKey}/{platform}/config`.
- New error codes (string values, used with existing `AdsMcpError`): `PLATFORM_NOT_CONFIGURED` (status 404), `PLATFORM_CONFIG_INCOMPLETE` (status 409).
- HMAC signing scheme is unchanged (`shared/auth.py`): canonical request is `METHOD\npath\ntimestamp\nonce\nrequest_id\nbody_sha256_hex`, headers `X-AdsMcp-Key-Id / -Timestamp / -Nonce / -Signature` + `X-Request-Id`.
- Commit after every task. Commit messages end with:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01LtzqttzZ4ohj9sayLh8xBk
  ```

---

## File Structure

**New files**

| File | Responsibility |
| --- | --- |
| `pytest.ini` | Pytest config: testpaths, rootdir path insert. |
| `tests/conftest.py` | Shared fixtures: in-memory secrets backend, `clients/` tmp dir redirect, env cleanup. |
| `tests/shared/test_manifest.py` | Unit tests for `shared/manifest.py`. |
| `tests/shared/test_runtime_config_manifest.py` | Unit tests for manifest-aware config loading. |
| `tests/shared/test_auth_admin_paths.py` | `/admin/` is signature-gated. |
| `tests/scripts/test_onboard_core.py` | Unit tests for `scripts/_onboard_core.py`. |
| `tests/servers/test_orchestrator_admin.py` | Admin router: signing, dry-run, redaction, round-trip. |
| `shared/manifest.py` | Manifest models glue: load/list/readiness/upsert/delete, platform-key constants, `REQUIRED_KEYS_BY_PLATFORM`, metadata merge. |
| `scripts/_onboard_core.py` | Pure onboarding logic shared by CLI + admin router + backfill: build manifest from inputs, write manifest + credential secrets + index, validate a platform. |
| `scripts/onboard-client.py` | CLI wrapper over `_onboard_core` (subcommands add/update/show/list/enable/disable/validate). |
| `scripts/backfill-manifests.py` | One-time: derive manifests from existing secrets + `local-dev-config.json`, write index. |
| `servers/orchestrator/admin.py` | FastAPI `APIRouter` for `/admin/clients*` — thin HTTP layer over `_onboard_core` + `shared/manifest`. |
| `clients/rnr-electrician.json` | Committed dev manifest (metadata only). |
| `clients/gq-painting.json` | Committed dev manifest (metadata only). |
| `servers/content-agent/brands/_TEMPLATE.md` | Brand-file template. |
| `docs/ONBOARDING.md` | Primary onboarding guide. |
| `docs/CLIENT_MANIFEST.md` | Manifest schema + storage reference. |

**Modified files**

| File | Change |
| --- | --- |
| `shared/models.py` | Add `PlatformConfig`, `ClientManifest`, `PlatformReadiness`, `ReadinessReport`. |
| `shared/secrets.py` | Add `put_secret`, `delete_secret` (boto3 writers + cache invalidation). |
| `shared/runtime_config.py` | `load_platform_runtime_config` consults the manifest; new error codes; metadata merge. |
| `shared/errors.py` | Docstring note listing the two new error codes (no structural change). |
| `servers/orchestrator/main.py` | `app.include_router(admin_router)`. |
| `servers/orchestrator/mcp_server.py` | Add `list_clients` + `get_client_status` MCP tools. |
| `shared/auth.py` | Signature gate covers `/admin/` in addition to `/tools/`. |
| `scripts/push-secrets.py` | Delegate to `_onboard_core`; stop force-writing empty `google-ads` secret. |
| `servers/content-agent/mcp_server.py` | Emit a `warnings` entry when brand file missing but manifest exists. |
| `docs/INTEGRATION_CONTRACT.md` | Add `/admin/clients*` endpoints. |
| `README.md` | Replace "Create your local credentials file" with pointer to `docs/ONBOARDING.md`. |
| `CLAUDE.md` | Build Order item 13; Standing Rule about the manifest. |

---

## Task 1: Test harness + secrets writers

**Files:**
- Create: `pytest.ini`
- Create: `tests/conftest.py`
- Create: `tests/shared/test_secrets_writers.py`
- Modify: `shared/secrets.py`

**Interfaces:**
- Consumes: `shared/config.py` `Settings`, `get_settings`.
- Produces:
  - `shared.secrets.put_secret(secret_id: str, value, settings: Settings) -> None` — creates or updates a Secrets Manager secret; JSON-encodes `dict`/`list`, passes `str` through; invalidates the in-process cache entry.
  - `shared.secrets.delete_secret(secret_id: str, settings: Settings) -> None` — deletes with `ForceDeleteWithoutRecovery=True`; no error if already absent; invalidates cache.
  - pytest fixture `fake_secrets` (autouse): monkeypatches `shared.secrets` module-level `get_secret`, `put_secret`, `delete_secret` to operate on an in-memory `dict[str, Any]`, exposes `.store`.
  - pytest fixture `clients_dir` (autouse): points manifest local-file lookup at a tmp dir via env `ADS_MCP_CLIENTS_DIR`.

- [ ] **Step 1: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
addopts = -q
pythonpath = .
```

- [ ] **Step 2: Write the failing test** — `tests/shared/test_secrets_writers.py`

```python
from __future__ import annotations

import json

from shared.config import get_settings
from shared import secrets as secrets_mod


def test_put_secret_json_encodes_dicts(fake_secrets):
    settings = get_settings()
    secrets_mod.put_secret("/ads-mcp/acme/manifest", {"businessKey": "acme"}, settings)
    assert fake_secrets.store["/ads-mcp/acme/manifest"] == {"businessKey": "acme"}


def test_get_after_put_roundtrips(fake_secrets):
    settings = get_settings()
    secrets_mod.put_secret("/ads-mcp/_index/clients", ["acme"], settings)
    assert secrets_mod.get_secret("/ads-mcp/_index/clients", settings) == ["acme"]


def test_delete_secret_is_idempotent(fake_secrets):
    settings = get_settings()
    secrets_mod.delete_secret("/ads-mcp/missing/manifest", settings)  # no raise
    secrets_mod.put_secret("/ads-mcp/acme/manifest", {"x": 1}, settings)
    secrets_mod.delete_secret("/ads-mcp/acme/manifest", settings)
    assert "/ads-mcp/acme/manifest" not in fake_secrets.store
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


class _FakeSecrets:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def get_secret(self, secret_id: str, settings) -> Any:  # noqa: ANN001
        return self.store.get(secret_id)

    def put_secret(self, secret_id: str, value: Any, settings) -> None:  # noqa: ANN001
        self.store[secret_id] = value

    def delete_secret(self, secret_id: str, settings) -> None:  # noqa: ANN001
        self.store.pop(secret_id, None)


@pytest.fixture(autouse=True)
def fake_secrets(monkeypatch) -> _FakeSecrets:
    fake = _FakeSecrets()
    monkeypatch.setattr("shared.secrets.get_secret", fake.get_secret)
    monkeypatch.setattr("shared.secrets.put_secret", fake.put_secret)
    monkeypatch.setattr("shared.secrets.delete_secret", fake.delete_secret)
    # Ensure a region is set so Settings.aws_region is truthy.
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    return fake


@pytest.fixture(autouse=True)
def clients_dir(monkeypatch, tmp_path) -> Path:
    d = tmp_path / "clients"
    d.mkdir()
    monkeypatch.setenv("ADS_MCP_CLIENTS_DIR", str(d))
    monkeypatch.delenv("ADS_MCP_CLIENT_MANIFESTS_JSON", raising=False)
    return d


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from shared.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

Note: because `fake_secrets` monkeypatches `shared.secrets.get_secret`, any module that does `from shared.secrets import get_secret` at import time would keep the original. **All production code must call `shared.secrets.get_secret(...)` / `secrets_mod.get_secret(...)` via the module, not via `from ... import get_secret`.** `shared/runtime_config.py` currently does `from shared.secrets import get_platform_config` — Task 5 changes it to `from shared import secrets as secrets_mod`.

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/shared/test_secrets_writers.py -v`
Expected: FAIL — `AttributeError: module 'shared.secrets' has no attribute 'put_secret'`

- [ ] **Step 5: Implement `put_secret` / `delete_secret` in `shared/secrets.py`**

Add after `get_secret`:

```python
def _invalidate_cache(secret_id: str) -> None:
    _SECRET_CACHE.pop(secret_id, None)


def put_secret(secret_id: str, value: Any, settings: Settings) -> None:
    """Create or update a Secrets Manager secret. dict/list are JSON-encoded."""
    if isinstance(value, (dict, list)):
        secret_string = json.dumps(value)
    else:
        secret_string = str(value)

    boto3 = import_module("boto3")
    client = boto3.client("secretsmanager", region_name=settings.aws_region)
    try:
        client.create_secret(Name=secret_id, SecretString=secret_string)
    except Exception as exc:  # noqa: BLE001
        aws_code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", "")
        if aws_code in ("ResourceExistsException",):
            client.put_secret_value(SecretId=secret_id, SecretString=secret_string)
        else:
            from shared.errors import AdsMcpError

            raise AdsMcpError(
                status_code=500,
                error_code="INTERNAL_ERROR",
                message="Failed to write configuration to secrets store.",
                details={"reason": str(exc)},
            ) from exc
    _invalidate_cache(secret_id)


def delete_secret(secret_id: str, settings: Settings) -> None:
    """Delete a secret. No error if it does not exist."""
    boto3 = import_module("boto3")
    client = boto3.client("secretsmanager", region_name=settings.aws_region)
    try:
        client.delete_secret(
            SecretId=secret_id, ForceDeleteWithoutRecovery=True
        )
    except Exception as exc:  # noqa: BLE001
        aws_code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", "")
        if aws_code in ("ResourceNotFoundException", "SecretNotFoundException"):
            pass
        else:
            from shared.errors import AdsMcpError

            raise AdsMcpError(
                status_code=500,
                error_code="INTERNAL_ERROR",
                message="Failed to delete configuration from secrets store.",
                details={"reason": str(exc)},
            ) from exc
    _invalidate_cache(secret_id)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/shared/test_secrets_writers.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add pytest.ini tests/conftest.py tests/shared/test_secrets_writers.py shared/secrets.py
git commit -m "feat(secrets): add put_secret/delete_secret + pytest harness"
```

---

## Task 2: Manifest models

**Files:**
- Modify: `shared/models.py`
- Test: `tests/shared/test_manifest.py` (models section only; file continues in Task 3–4)

**Interfaces:**
- Consumes: Pydantic v2 (`BaseModel`, `Field`, `ConfigDict`), `typing.Literal`.
- Produces:
  - `PlatformConfig(BaseModel)` — `enabled: bool = False`; `model_config = ConfigDict(extra="allow")`; method `metadata() -> dict[str, Any]` returning all set fields except `enabled`.
  - `ClientManifest(BaseModel)` — fields `businessKey: str`, `displayName: str`, `tenantId: int | None = None`, `industry: str | None = None`, `status: Literal["active","paused","archived"] = "active"`, `platforms: dict[str, PlatformConfig]`, `createdAt: str | None = None`, `updatedAt: str | None = None`. A `model_validator(mode="after")` fills every key in `KNOWN_PLATFORMS` that is missing with `PlatformConfig(enabled=False)`. Method `public_dict() -> dict` returns `model_dump()` unchanged (manifests contain no secrets — but this is the single serializer the admin layer must use).
  - `PlatformReadiness(BaseModel)` — `enabled: bool`, `ready: bool`, `missingKeys: list[str] = []`.
  - `ReadinessReport(BaseModel)` — `businessKey: str`, `displayName: str`, `status: str`, `platforms: dict[str, PlatformReadiness]`.
- Note: `KNOWN_PLATFORMS` is imported from `shared.manifest` (Task 3). To avoid a circular import, define `KNOWN_PLATFORMS` in `shared/models.py` and have `shared/manifest.py` re-export it.

- [ ] **Step 1: Write the failing test** — create `tests/shared/test_manifest.py`

```python
from __future__ import annotations

from shared.models import ClientManifest, PlatformConfig, KNOWN_PLATFORMS


def test_manifest_fills_missing_platform_keys():
    m = ClientManifest(
        businessKey="acme",
        displayName="Acme",
        platforms={"google-ads": PlatformConfig(enabled=True, customer_account_id="123")},
    )
    assert set(m.platforms) == set(KNOWN_PLATFORMS)
    assert m.platforms["analytics"].enabled is False
    assert m.platforms["google-ads"].metadata() == {"customer_account_id": "123"}


def test_known_platforms_order():
    assert KNOWN_PLATFORMS == (
        "google-ads", "analytics", "search-console", "gbp", "meta-ads",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/shared/test_manifest.py -v`
Expected: FAIL — `ImportError: cannot import name 'ClientManifest' from 'shared.models'`

- [ ] **Step 3: Implement models** — append to `shared/models.py`

```python
from typing import Literal

from pydantic import ConfigDict, model_validator

KNOWN_PLATFORMS: tuple[str, ...] = (
    "google-ads",
    "analytics",
    "search-console",
    "gbp",
    "meta-ads",
)


class PlatformConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False

    def metadata(self) -> dict[str, Any]:
        dumped = self.model_dump()
        dumped.pop("enabled", None)
        return {k: v for k, v in dumped.items() if v is not None}


class ClientManifest(BaseModel):
    businessKey: str
    displayName: str
    tenantId: int | None = None
    industry: str | None = None
    status: Literal["active", "paused", "archived"] = "active"
    platforms: dict[str, PlatformConfig] = Field(default_factory=dict)
    createdAt: str | None = None
    updatedAt: str | None = None

    @model_validator(mode="after")
    def _fill_platforms(self) -> "ClientManifest":
        for key in KNOWN_PLATFORMS:
            self.platforms.setdefault(key, PlatformConfig(enabled=False))
        return self

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump()


class PlatformReadiness(BaseModel):
    enabled: bool
    ready: bool
    missingKeys: list[str] = Field(default_factory=list)


class ReadinessReport(BaseModel):
    businessKey: str
    displayName: str
    status: str
    platforms: dict[str, PlatformReadiness]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/shared/test_manifest.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add shared/models.py tests/shared/test_manifest.py
git commit -m "feat(models): add ClientManifest + readiness models"
```

---

## Task 3: Manifest load / list / precedence

**Files:**
- Create: `shared/manifest.py`
- Test: `tests/shared/test_manifest.py` (append)

**Interfaces:**
- Consumes: `shared.models` (`ClientManifest`, `PlatformConfig`, `KNOWN_PLATFORMS`, `ReadinessReport`, `PlatformReadiness`), `shared.config.get_settings`, `shared.secrets` (module import), `shared.errors.AdsMcpError`.
- Produces:
  - `KNOWN_PLATFORMS` (re-export), `REQUIRED_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]]`.
  - `manifest_secret_id(business_key: str) -> str` → `f"/ads-mcp/{business_key}/manifest"`.
  - `INDEX_SECRET_ID = "/ads-mcp/_index/clients"`.
  - `load_client_manifest(business_key: str) -> ClientManifest | None` — precedence: env `ADS_MCP_CLIENT_MANIFESTS_JSON` (JSON object keyed by businessKey) → local file `{clients_dir}/{business_key}.json` → secret `manifest_secret_id(...)`. First hit wins. `clients_dir` = env `ADS_MCP_CLIENTS_DIR` or `<repo_root>/clients`.
  - `list_client_manifests(*, include_inactive: bool = False) -> list[ClientManifest]` — union of: keys in the env JSON, `*.json` stems in `clients_dir`, entries in the index secret. Deduped by `businessKey` (precedence as above for the actual content). Sorted by `businessKey`. Filters `status != "active"` unless `include_inactive`.

- [ ] **Step 1: Write the failing tests** — append to `tests/shared/test_manifest.py`

```python
import json

from shared import manifest as manifest_mod
from shared.config import get_settings


def _write_local(clients_dir, key, data):
    (clients_dir / f"{key}.json").write_text(json.dumps(data))


def test_load_prefers_env_over_file_over_secret(clients_dir, fake_secrets, monkeypatch):
    _write_local(clients_dir, "acme", {"businessKey": "acme", "displayName": "File"})
    fake_secrets.store[manifest_mod.manifest_secret_id("acme")] = {
        "businessKey": "acme", "displayName": "Secret",
    }
    monkeypatch.setenv(
        "ADS_MCP_CLIENT_MANIFESTS_JSON",
        json.dumps({"acme": {"businessKey": "acme", "displayName": "Env"}}),
    )
    assert manifest_mod.load_client_manifest("acme").displayName == "Env"


def test_load_falls_back_to_file_then_secret(clients_dir, fake_secrets):
    fake_secrets.store[manifest_mod.manifest_secret_id("acme")] = {
        "businessKey": "acme", "displayName": "Secret",
    }
    assert manifest_mod.load_client_manifest("acme").displayName == "Secret"
    _write_local(clients_dir, "acme", {"businessKey": "acme", "displayName": "File"})
    assert manifest_mod.load_client_manifest("acme").displayName == "File"


def test_load_missing_returns_none(clients_dir, fake_secrets):
    assert manifest_mod.load_client_manifest("nope") is None


def test_list_unions_and_dedupes(clients_dir, fake_secrets):
    _write_local(clients_dir, "acme", {"businessKey": "acme", "displayName": "Acme"})
    fake_secrets.store[manifest_mod.INDEX_SECRET_ID] = ["acme", "beta"]
    fake_secrets.store[manifest_mod.manifest_secret_id("beta")] = {
        "businessKey": "beta", "displayName": "Beta",
    }
    keys = [m.businessKey for m in manifest_mod.list_client_manifests()]
    assert keys == ["acme", "beta"]


def test_list_excludes_inactive_by_default(clients_dir, fake_secrets):
    _write_local(clients_dir, "acme", {"businessKey": "acme", "displayName": "A", "status": "archived"})
    assert manifest_mod.list_client_manifests() == []
    assert len(manifest_mod.list_client_manifests(include_inactive=True)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/shared/test_manifest.py -v -k "load or list"`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.manifest'`

- [ ] **Step 3: Implement `shared/manifest.py`**

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from shared import secrets as secrets_mod
from shared.config import get_settings
from shared.models import (
    KNOWN_PLATFORMS,
    ClientManifest,
    PlatformReadiness,
    ReadinessReport,
)

__all__ = [
    "KNOWN_PLATFORMS",
    "REQUIRED_KEYS_BY_PLATFORM",
    "INDEX_SECRET_ID",
    "manifest_secret_id",
    "credential_secret_id",
    "load_client_manifest",
    "list_client_manifests",
    "client_readiness",
    "merge_platform_config",
    "upsert_client_manifest",
    "set_platform_config",
    "delete_client_manifest",
]

INDEX_SECRET_ID = "/ads-mcp/_index/clients"

# Full credential-level requirements per platform. Metadata keys
# (customer_account_id, property_id, site_url, gbp_location_id, ad_account_id)
# may be supplied by the manifest OR the credential secret; readiness checks
# the merged view.
REQUIRED_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "google-ads": (
        "customer_account_id",
        "developer_token",
        "client_id",
        "client_secret",
        "refresh_token",
    ),
    "analytics": ("property_id", "client_id", "client_secret", "refresh_token"),
    "search-console": ("site_url", "client_id", "client_secret", "refresh_token"),
    "gbp": ("client_id", "client_secret", "refresh_token", "gbp_location_id"),
    "meta-ads": ("ad_account_id", "access_token"),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _clients_dir() -> Path:
    override = os.getenv("ADS_MCP_CLIENTS_DIR")
    return Path(override) if override else _repo_root() / "clients"


def manifest_secret_id(business_key: str) -> str:
    return f"/ads-mcp/{business_key}/manifest"


def credential_secret_id(business_key: str, platform: str) -> str:
    return f"/ads-mcp/{business_key}/{platform}/config"


def _env_manifests() -> dict[str, Any]:
    raw = os.getenv("ADS_MCP_CLIENT_MANIFESTS_JSON")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _raw_manifest(business_key: str) -> dict[str, Any] | None:
    env = _env_manifests()
    if business_key in env and isinstance(env[business_key], dict):
        return env[business_key]

    local = _clients_dir() / f"{business_key}.json"
    if local.is_file():
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    settings = get_settings()
    secret = secrets_mod.get_secret(manifest_secret_id(business_key), settings)
    if isinstance(secret, dict):
        return secret
    return None


def load_client_manifest(business_key: str) -> ClientManifest | None:
    raw = _raw_manifest(business_key)
    if raw is None:
        return None
    raw.setdefault("businessKey", business_key)
    return ClientManifest.model_validate(raw)


def _known_business_keys() -> list[str]:
    keys: set[str] = set(_env_manifests().keys())
    d = _clients_dir()
    if d.is_dir():
        keys.update(p.stem for p in d.glob("*.json"))
    settings = get_settings()
    index = secrets_mod.get_secret(INDEX_SECRET_ID, settings)
    if isinstance(index, list):
        keys.update(str(k) for k in index)
    return sorted(keys)


def list_client_manifests(*, include_inactive: bool = False) -> list[ClientManifest]:
    out: list[ClientManifest] = []
    for key in _known_business_keys():
        manifest = load_client_manifest(key)
        if manifest is None:
            continue
        if not include_inactive and manifest.status != "active":
            continue
        out.append(manifest)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/shared/test_manifest.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add shared/manifest.py tests/shared/test_manifest.py
git commit -m "feat(manifest): load + list with env/file/secret precedence"
```

---

## Task 4: Readiness, metadata merge, upsert/delete

**Files:**
- Modify: `shared/manifest.py`
- Test: `tests/shared/test_manifest.py` (append)

**Interfaces:**
- Consumes: Task 3 functions, `shared.secrets.put_secret` / `delete_secret`, `datetime`.
- Produces:
  - `merge_platform_config(manifest: ClientManifest | None, platform: str, secret_config: dict | None) -> dict | None` — returns `{**secret_config, **manifest_metadata}` (manifest metadata wins for non-secret ids); `None` only if both are `None`/absent.
  - `client_readiness(business_key: str) -> ReadinessReport | None` — `None` if no manifest. For each `KNOWN_PLATFORMS`: `enabled` from manifest; `ready = enabled and no missingKeys`; `missingKeys` = `REQUIRED_KEYS_BY_PLATFORM[platform]` absent/falsy from the merged (manifest ∪ credential-secret) dict; when `enabled` is `False`, `missingKeys = []` and `ready = False`.
  - `upsert_client_manifest(manifest: ClientManifest, *, dry_run: bool = False) -> ClientManifest` — stamps `createdAt` (if new) + `updatedAt` (UTC ISO `...Z`); when not dry-run writes `manifest_secret_id` and adds the key to `INDEX_SECRET_ID`. Returns the stamped manifest.
  - `set_platform_config(business_key: str, platform: str, *, enabled: bool, metadata: dict | None = None, dry_run: bool = False) -> ClientManifest` — loads existing (or 404 `AdsMcpError` if none), mutates one platform, calls `upsert_client_manifest`.
  - `delete_client_manifest(business_key: str, *, dry_run: bool = False) -> None` — removes manifest secret + index entry; leaves credential secrets untouched.

- [ ] **Step 1: Write the failing tests** — append to `tests/shared/test_manifest.py`

```python
import pytest
from shared.errors import AdsMcpError


def test_merge_platform_config_manifest_wins_on_ids(clients_dir, fake_secrets):
    m = manifest_mod.load_client_manifest("acme")  # None ok for this call shape
    merged = manifest_mod.merge_platform_config(
        None, "google-ads", {"customer_account_id": "old", "refresh_token": "rt"}
    )
    assert merged == {"customer_account_id": "old", "refresh_token": "rt"}


def test_readiness_none_without_manifest(clients_dir, fake_secrets):
    assert manifest_mod.client_readiness("acme") is None


def test_readiness_enabled_but_incomplete(clients_dir, fake_secrets):
    _write_local(clients_dir, "acme", {
        "businessKey": "acme", "displayName": "Acme",
        "platforms": {"search-console": {"enabled": True, "site_url": "https://a.com"}},
    })
    # credential secret only has client_id -> missing client_secret, refresh_token
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "search-console")] = {
        "client_id": "cid",
    }
    report = manifest_mod.client_readiness("acme")
    sc = report.platforms["search-console"]
    assert sc.enabled is True and sc.ready is False
    assert set(sc.missingKeys) == {"client_secret", "refresh_token"}
    assert report.platforms["analytics"].enabled is False
    assert report.platforms["analytics"].missingKeys == []


def test_readiness_ready_when_complete(clients_dir, fake_secrets):
    _write_local(clients_dir, "acme", {
        "businessKey": "acme", "displayName": "Acme",
        "platforms": {"search-console": {"enabled": True}},
    })
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "search-console")] = {
        "site_url": "https://a.com", "client_id": "c", "client_secret": "s", "refresh_token": "r",
    }
    assert manifest_mod.client_readiness("acme").platforms["search-console"].ready is True


def test_upsert_stamps_timestamps_and_index(clients_dir, fake_secrets):
    from shared.models import ClientManifest
    m = ClientManifest(businessKey="acme", displayName="Acme", platforms={})
    saved = manifest_mod.upsert_client_manifest(m)
    assert saved.createdAt and saved.updatedAt
    assert fake_secrets.store[manifest_mod.INDEX_SECRET_ID] == ["acme"]
    assert fake_secrets.store[manifest_mod.manifest_secret_id("acme")]["displayName"] == "Acme"


def test_upsert_dry_run_writes_nothing(clients_dir, fake_secrets):
    from shared.models import ClientManifest
    m = ClientManifest(businessKey="acme", displayName="Acme", platforms={})
    manifest_mod.upsert_client_manifest(m, dry_run=True)
    assert manifest_mod.manifest_secret_id("acme") not in fake_secrets.store
    assert manifest_mod.INDEX_SECRET_ID not in fake_secrets.store


def test_set_platform_config_requires_existing_manifest(clients_dir, fake_secrets):
    with pytest.raises(AdsMcpError) as exc:
        manifest_mod.set_platform_config("ghost", "gbp", enabled=True)
    assert exc.value.status_code == 404


def test_delete_removes_manifest_and_index_entry(clients_dir, fake_secrets):
    from shared.models import ClientManifest
    manifest_mod.upsert_client_manifest(ClientManifest(businessKey="acme", displayName="A", platforms={}))
    manifest_mod.upsert_client_manifest(ClientManifest(businessKey="beta", displayName="B", platforms={}))
    manifest_mod.delete_client_manifest("acme")
    assert manifest_mod.manifest_secret_id("acme") not in fake_secrets.store
    assert fake_secrets.store[manifest_mod.INDEX_SECRET_ID] == ["beta"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/shared/test_manifest.py -v -k "merge or readiness or upsert or set_platform or delete"`
Expected: FAIL — `AttributeError: module 'shared.manifest' has no attribute 'merge_platform_config'`

- [ ] **Step 3: Implement — append to `shared/manifest.py`**

```python
from datetime import UTC, datetime

from shared.errors import AdsMcpError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def merge_platform_config(
    manifest: ClientManifest | None,
    platform: str,
    secret_config: dict | None,
) -> dict | None:
    meta: dict[str, Any] = {}
    if manifest is not None:
        pc = manifest.platforms.get(platform)
        if pc is not None:
            meta = pc.metadata()
    if secret_config is None and not meta:
        return None
    return {**(secret_config or {}), **meta}


def _credential_secret(business_key: str, platform: str) -> dict | None:
    settings = get_settings()
    value = secrets_mod.get_secret(credential_secret_id(business_key, platform), settings)
    return value if isinstance(value, dict) else None


def client_readiness(business_key: str) -> ReadinessReport | None:
    manifest = load_client_manifest(business_key)
    if manifest is None:
        return None
    platforms: dict[str, PlatformReadiness] = {}
    for platform in KNOWN_PLATFORMS:
        pc = manifest.platforms.get(platform)
        enabled = bool(pc and pc.enabled)
        if not enabled:
            platforms[platform] = PlatformReadiness(enabled=False, ready=False, missingKeys=[])
            continue
        merged = merge_platform_config(manifest, platform, _credential_secret(business_key, platform)) or {}
        missing = [k for k in REQUIRED_KEYS_BY_PLATFORM[platform] if not merged.get(k)]
        platforms[platform] = PlatformReadiness(
            enabled=True, ready=not missing, missingKeys=missing
        )
    return ReadinessReport(
        businessKey=manifest.businessKey,
        displayName=manifest.displayName,
        status=manifest.status,
        platforms=platforms,
    )


def _add_to_index(business_key: str) -> None:
    settings = get_settings()
    current = secrets_mod.get_secret(INDEX_SECRET_ID, settings)
    keys = set(current) if isinstance(current, list) else set()
    if business_key not in keys:
        keys.add(business_key)
        secrets_mod.put_secret(INDEX_SECRET_ID, sorted(keys), settings)


def _remove_from_index(business_key: str) -> None:
    settings = get_settings()
    current = secrets_mod.get_secret(INDEX_SECRET_ID, settings)
    if not isinstance(current, list):
        return
    remaining = sorted(k for k in current if k != business_key)
    secrets_mod.put_secret(INDEX_SECRET_ID, remaining, settings)


def upsert_client_manifest(
    manifest: ClientManifest, *, dry_run: bool = False
) -> ClientManifest:
    existing = load_client_manifest(manifest.businessKey)
    now = _now_iso()
    manifest.createdAt = (existing.createdAt if existing and existing.createdAt else manifest.createdAt) or now
    manifest.updatedAt = now
    if dry_run:
        return manifest
    settings = get_settings()
    secrets_mod.put_secret(manifest_secret_id(manifest.businessKey), manifest.model_dump(), settings)
    _add_to_index(manifest.businessKey)
    return manifest


def set_platform_config(
    business_key: str,
    platform: str,
    *,
    enabled: bool,
    metadata: dict | None = None,
    dry_run: bool = False,
) -> ClientManifest:
    if platform not in KNOWN_PLATFORMS:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"Unknown platform '{platform}'.",
            details={"knownPlatforms": list(KNOWN_PLATFORMS)},
        )
    manifest = load_client_manifest(business_key)
    if manifest is None:
        raise AdsMcpError(
            status_code=404,
            error_code="PLATFORM_NOT_CONFIGURED",
            message=f"No client manifest for businessKey '{business_key}'. Run onboard-client.py add first.",
            details={"businessKey": business_key},
        )
    from shared.models import PlatformConfig

    manifest.platforms[platform] = PlatformConfig(enabled=enabled, **(metadata or {}))
    return upsert_client_manifest(manifest, dry_run=dry_run)


def delete_client_manifest(business_key: str, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    settings = get_settings()
    secrets_mod.delete_secret(manifest_secret_id(business_key), settings)
    _remove_from_index(business_key)
```

Add the new names to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/shared/test_manifest.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add shared/manifest.py tests/shared/test_manifest.py
git commit -m "feat(manifest): readiness, metadata merge, upsert/delete"
```

---

## Task 5: Manifest-aware platform config loading

**Files:**
- Modify: `shared/runtime_config.py`
- Modify: `shared/errors.py` (docstring only)
- Test: `tests/shared/test_runtime_config_manifest.py`

**Interfaces:**
- Consumes: `shared.manifest.load_client_manifest`, `merge_platform_config`, `REQUIRED_KEYS_BY_PLATFORM`; `shared.errors.AdsMcpError`.
- Produces: unchanged public signatures — `load_platform_runtime_config(platform, business_key, required_keys=(), tool=None) -> dict`, `load_google_ads_config`, `load_google_ads_sdk_config`. New behavior per the table below. `_load_env_platform_configs` unchanged.

**Behavior table (implement exactly):**

| Manifest | Platform enabled? | Secret/env config | Result |
| --- | --- | --- | --- |
| absent | – | present | unchanged (return merged config, 500 on missing `required_keys` — pre-existing behavior kept) |
| absent | – | absent | unchanged `400 REQUEST_INVALID` "No {platform} configuration found" |
| present | `false`/missing | any | `404 PLATFORM_NOT_CONFIGURED`, message `"{displayName} is not set up for {platform}. Onboard it with scripts/onboard-client.py or the admin dashboard."`, `details={"businessKey","platform"}` |
| present | `true` | env/secret present, merged still missing a `required_keys` entry | `409 PLATFORM_CONFIG_INCOMPLETE`, `details={"businessKey","platform","missingKeys":[...]}` |
| present | `true` | env/secret absent entirely | `409 PLATFORM_CONFIG_INCOMPLETE` with all `required_keys` (minus any the manifest metadata supplies) as `missingKeys` |
| present | `true` | complete after merge | return `{**config, **manifest_metadata}` |

- [ ] **Step 1: Write the failing tests** — `tests/shared/test_runtime_config_manifest.py`

```python
from __future__ import annotations

import json

import pytest

from shared import manifest as manifest_mod
from shared.errors import AdsMcpError
from shared.runtime_config import load_platform_runtime_config


def _manifest(clients_dir, key, platforms):
    (clients_dir / f"{key}.json").write_text(json.dumps({
        "businessKey": key, "displayName": key.title().replace("-", " "),
        "platforms": platforms,
    }))


def test_no_manifest_keeps_legacy_behavior(clients_dir, fake_secrets, monkeypatch):
    monkeypatch.setenv(
        "ADS_MCP_ANALYTICS_CONFIGS_JSON",
        json.dumps({"acme": {"property_id": "1", "client_id": "c", "client_secret": "s", "refresh_token": "r"}}),
    )
    cfg = load_platform_runtime_config(
        platform="analytics", business_key="acme",
        required_keys=("property_id", "client_id", "client_secret", "refresh_token"),
    )
    assert cfg["property_id"] == "1"


def test_no_manifest_no_config_is_400(clients_dir, fake_secrets):
    with pytest.raises(AdsMcpError) as exc:
        load_platform_runtime_config(platform="gbp", business_key="acme")
    assert exc.value.status_code == 400
    assert exc.value.error_code == "REQUEST_INVALID"


def test_platform_disabled_is_404(clients_dir, fake_secrets):
    _manifest(clients_dir, "acme", {"google-ads": {"enabled": True}})
    with pytest.raises(AdsMcpError) as exc:
        load_platform_runtime_config(platform="analytics", business_key="acme")
    assert exc.value.status_code == 404
    assert exc.value.error_code == "PLATFORM_NOT_CONFIGURED"
    assert "not set up for analytics" in exc.value.message


def test_platform_enabled_incomplete_is_409(clients_dir, fake_secrets):
    _manifest(clients_dir, "acme", {"search-console": {"enabled": True, "site_url": "https://a.com"}})
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "search-console")] = {"client_id": "c"}
    with pytest.raises(AdsMcpError) as exc:
        load_platform_runtime_config(
            platform="search-console", business_key="acme",
            required_keys=("site_url", "client_id", "client_secret", "refresh_token"),
        )
    assert exc.value.status_code == 409
    assert exc.value.error_code == "PLATFORM_CONFIG_INCOMPLETE"
    assert set(exc.value.details["missingKeys"]) == {"client_secret", "refresh_token"}


def test_platform_enabled_complete_merges_manifest_metadata(clients_dir, fake_secrets):
    _manifest(clients_dir, "acme", {"google-ads": {"enabled": True, "customer_account_id": "111", "manager_account_id": "999"}})
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "google-ads")] = {
        "customer_account_id": "OLD", "developer_token": "d",
        "client_id": "c", "client_secret": "s", "refresh_token": "r",
    }
    cfg = load_platform_runtime_config(
        platform="google-ads", business_key="acme",
        required_keys=("customer_account_id", "developer_token", "client_id", "client_secret", "refresh_token"),
    )
    assert cfg["customer_account_id"] == "111"  # manifest wins
    assert cfg["manager_account_id"] == "999"
    assert cfg["refresh_token"] == "r"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/shared/test_runtime_config_manifest.py -v`
Expected: FAIL — disabled case returns 500/400, not 404.

- [ ] **Step 3: Rewrite `load_platform_runtime_config` in `shared/runtime_config.py`**

Replace the function body (keep imports; add `from shared import manifest as manifest_mod`):

```python
def load_platform_runtime_config(
    *,
    platform: str,
    business_key: str,
    required_keys: tuple[str, ...] = (),
    tool: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    env_configs = _load_env_platform_configs(platform)

    config: Any | None = env_configs.get(business_key)
    if config is None:
        config = get_platform_config(platform, business_key, settings)

    client_manifest = manifest_mod.load_client_manifest(business_key)

    if client_manifest is not None:
        platform_cfg = client_manifest.platforms.get(platform)
        if platform_cfg is None or not platform_cfg.enabled:
            raise AdsMcpError(
                status_code=404,
                error_code="PLATFORM_NOT_CONFIGURED",
                message=(
                    f"{client_manifest.displayName} is not set up for {platform}. "
                    f"Onboard it with scripts/onboard-client.py or the admin dashboard."
                ),
                tool=tool,
                details={"businessKey": business_key, "platform": platform},
            )

        merged = manifest_mod.merge_platform_config(
            client_manifest, platform, config if isinstance(config, dict) else None
        ) or {}
        missing_keys = [key for key in required_keys if not merged.get(key)]
        if missing_keys:
            raise AdsMcpError(
                status_code=409,
                error_code="PLATFORM_CONFIG_INCOMPLETE",
                message=(
                    f"{platform} configuration for '{business_key}' is incomplete."
                ),
                tool=tool,
                details={
                    "businessKey": business_key,
                    "platform": platform,
                    "missingKeys": missing_keys,
                },
            )
        return merged

    # ── No manifest: pre-existing behavior, unchanged ───────────────────────
    if config is None:
        raise AdsMcpError(
            status_code=400,
            error_code="REQUEST_INVALID",
            message=f"No {platform} configuration found for businessKey '{business_key}'.",
            tool=tool,
            details={"businessKey": business_key, "platform": platform},
        )
    if not isinstance(config, dict):
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message=f"{platform} configuration for businessKey '{business_key}' is malformed.",
            tool=tool,
            details={"businessKey": business_key, "platform": platform},
        )
    missing_keys = [key for key in required_keys if not config.get(key)]
    if missing_keys:
        raise AdsMcpError(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message=f"{platform} configuration for businessKey '{business_key}' is incomplete.",
            tool=tool,
            details={
                "businessKey": business_key,
                "platform": platform,
                "missingKeys": missing_keys,
            },
        )
    return config
```

- [ ] **Step 4: Update `shared/errors.py` docstring**

Add a module docstring at the top:

```python
"""Structured error type for ads-mcp services.

Known error codes include: REQUEST_INVALID, AUTH_INVALID, AUTH_EXPIRED,
INTERNAL_ERROR, PLATFORM_NOT_CONFIGURED (404 — client has no manifest entry
for the platform, or it is disabled), PLATFORM_CONFIG_INCOMPLETE (409 —
platform enabled but credential secret is missing required keys).
"""
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/shared/ -v`
Expected: PASS (all — Task 1–5 tests)

- [ ] **Step 6: Regression check — full existing import surface**

Run: `python -c "import servers.google-ads.main" 2>/dev/null || (cd servers/google-ads && python -c "import main")`
Then: `pytest -q`
Expected: no collection errors; all pass.

- [ ] **Step 7: Commit**

```bash
git add shared/runtime_config.py shared/errors.py tests/shared/test_runtime_config_manifest.py
git commit -m "feat(runtime-config): manifest-aware platform loading with 404/409"
```

---

## Task 6: Onboarding core module

**Files:**
- Create: `scripts/_onboard_core.py`
- Test: `tests/scripts/test_onboard_core.py`

**Interfaces:**
- Consumes: `shared.manifest` (all writers), `shared.models.ClientManifest` / `PlatformConfig`, `shared.config.get_settings`, `shared.secrets.put_secret`.
- Produces:
  - `CREDENTIAL_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]]` — the subset of `REQUIRED_KEYS_BY_PLATFORM` values that are *secret* (excludes `customer_account_id`, `manager_account_id`, `property_id`, `site_url`, `gbp_account_id`, `gbp_location_id`, `ad_account_id`).
  - `METADATA_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]]` — the non-secret ids per platform.
  - `PlatformInput` dataclass — `platform: str`, `enabled: bool`, `metadata: dict[str, str]`, `credentials: dict[str, str]`.
  - `build_manifest(business_key, display_name, *, tenant_id=None, industry=None, status="active", platform_inputs: list[PlatformInput], existing: ClientManifest | None = None) -> ClientManifest` — pure; no I/O.
  - `apply_onboarding(manifest: ClientManifest, platform_inputs: list[PlatformInput], *, dry_run: bool = False) -> dict` — writes manifest (via `upsert_client_manifest`) + each enabled platform's credential secret (merging onto any existing secret, only non-empty values) + index. Returns `{"manifest": <public_dict>, "secretsWritten": [ids], "dryRun": bool}`.
  - `derive_platform_inputs_from_flat(flat: dict) -> list[PlatformInput]` — maps the legacy `local-dev-config.json` per-business object to `PlatformInput`s (google-ads enabled iff `customer_account_id` present; analytics iff `ga4_property_id`; search-console iff `site_url`; gbp iff `gbp_account_id` and `gbp_location_id`; meta-ads always disabled).
  - `validate_platform(business_key: str, platform: str) -> tuple[bool, str]` — imports the relevant tool lazily, runs one cheap call, returns `(ok, detail)`. Never raises.

- [ ] **Step 1: Write the failing tests** — `tests/scripts/test_onboard_core.py`

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import _onboard_core as core
from shared import manifest as manifest_mod


def test_derive_from_flat_only_enables_present_platforms():
    flat = {
        "customer_account_id": "111", "manager_account_id": "999",
        "developer_token": "d", "client_id": "c", "client_secret": "s", "refresh_token": "r",
        "site_url": "https://a.com",
    }
    inputs = {pi.platform: pi for pi in core.derive_platform_inputs_from_flat(flat)}
    assert inputs["google-ads"].enabled is True
    assert inputs["search-console"].enabled is True
    assert inputs["analytics"].enabled is False
    assert inputs["gbp"].enabled is False
    assert inputs["meta-ads"].enabled is False
    assert inputs["google-ads"].metadata["customer_account_id"] == "111"
    assert inputs["google-ads"].credentials["refresh_token"] == "r"


def test_apply_onboarding_writes_only_enabled_secrets(clients_dir, fake_secrets):
    inputs = [
        core.PlatformInput("google-ads", True,
                           {"customer_account_id": "111"},
                           {"developer_token": "d", "client_id": "c", "client_secret": "s", "refresh_token": "r"}),
        core.PlatformInput("analytics", False, {}, {}),
    ]
    manifest = core.build_manifest("acme", "Acme", platform_inputs=inputs)
    result = core.apply_onboarding(manifest, inputs)
    assert manifest_mod.credential_secret_id("acme", "google-ads") in fake_secrets.store
    assert manifest_mod.credential_secret_id("acme", "analytics") not in fake_secrets.store
    assert fake_secrets.store[manifest_mod.INDEX_SECRET_ID] == ["acme"]
    stored = fake_secrets.store[manifest_mod.manifest_secret_id("acme")]
    assert stored["platforms"]["google-ads"]["enabled"] is True
    assert "customer_account_id" not in fake_secrets.store[manifest_mod.credential_secret_id("acme", "google-ads")] \
        or fake_secrets.store[manifest_mod.credential_secret_id("acme", "google-ads")].get("developer_token") == "d"


def test_apply_onboarding_dry_run_writes_nothing(clients_dir, fake_secrets):
    inputs = [core.PlatformInput("gbp", True, {"gbp_location_id": "loc"},
                                 {"client_id": "c", "client_secret": "s", "refresh_token": "r"})]
    manifest = core.build_manifest("acme", "Acme", platform_inputs=inputs)
    result = core.apply_onboarding(manifest, inputs, dry_run=True)
    assert result["dryRun"] is True
    assert fake_secrets.store == {}


def test_build_manifest_preserves_existing_created_at(clients_dir, fake_secrets):
    from shared.models import ClientManifest
    existing = ClientManifest(businessKey="acme", displayName="Old", createdAt="2020-01-01T00:00:00Z", platforms={})
    m = core.build_manifest("acme", "Acme", platform_inputs=[], existing=existing)
    assert m.createdAt == "2020-01-01T00:00:00Z"
    assert m.displayName == "Acme"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/scripts/test_onboard_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts._onboard_core'`
(If `scripts/` is not a package, add empty `scripts/__init__.py` in this step.)

- [ ] **Step 3: Implement `scripts/_onboard_core.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared import secrets as secrets_mod
from shared.config import get_settings
from shared.manifest import (
    KNOWN_PLATFORMS,
    REQUIRED_KEYS_BY_PLATFORM,
    credential_secret_id,
    load_client_manifest,
    upsert_client_manifest,
)
from shared.models import ClientManifest, PlatformConfig

METADATA_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "google-ads": ("customer_account_id", "manager_account_id"),
    "analytics": ("property_id",),
    "search-console": ("site_url",),
    "gbp": ("gbp_account_id", "gbp_location_id"),
    "meta-ads": ("ad_account_id",),
}

CREDENTIAL_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    platform: tuple(
        k for k in REQUIRED_KEYS_BY_PLATFORM[platform]
        if k not in METADATA_KEYS_BY_PLATFORM.get(platform, ())
    )
    for platform in KNOWN_PLATFORMS
}


@dataclass
class PlatformInput:
    platform: str
    enabled: bool
    metadata: dict[str, str] = field(default_factory=dict)
    credentials: dict[str, str] = field(default_factory=dict)


def derive_platform_inputs_from_flat(flat: dict) -> list[PlatformInput]:
    def creds(*extra: str) -> dict[str, str]:
        base = {k: flat[k] for k in ("client_id", "client_secret", "refresh_token") if flat.get(k)}
        for k in extra:
            if flat.get(k):
                base[k] = flat[k]
        return base

    inputs: list[PlatformInput] = []
    inputs.append(PlatformInput(
        "google-ads",
        bool(flat.get("customer_account_id")),
        {k: flat[k] for k in ("customer_account_id", "manager_account_id") if flat.get(k)},
        creds("developer_token"),
    ))
    inputs.append(PlatformInput(
        "analytics",
        bool(flat.get("ga4_property_id")),
        {"property_id": flat["ga4_property_id"]} if flat.get("ga4_property_id") else {},
        creds(),
    ))
    inputs.append(PlatformInput(
        "search-console",
        bool(flat.get("site_url")),
        {"site_url": flat["site_url"]} if flat.get("site_url") else {},
        creds(),
    ))
    inputs.append(PlatformInput(
        "gbp",
        bool(flat.get("gbp_account_id") and flat.get("gbp_location_id")),
        {k: flat[k] for k in ("gbp_account_id", "gbp_location_id") if flat.get(k)},
        creds(),
    ))
    inputs.append(PlatformInput("meta-ads", False, {}, {}))
    return inputs


def build_manifest(
    business_key: str,
    display_name: str,
    *,
    tenant_id: int | None = None,
    industry: str | None = None,
    status: str = "active",
    platform_inputs: list[PlatformInput],
    existing: ClientManifest | None = None,
) -> ClientManifest:
    platforms: dict[str, PlatformConfig] = {}
    by_platform = {pi.platform: pi for pi in platform_inputs}
    for platform in KNOWN_PLATFORMS:
        pi = by_platform.get(platform)
        if pi is None and existing is not None:
            platforms[platform] = existing.platforms.get(platform, PlatformConfig(enabled=False))
        elif pi is None:
            platforms[platform] = PlatformConfig(enabled=False)
        else:
            platforms[platform] = PlatformConfig(enabled=pi.enabled, **pi.metadata)
    return ClientManifest(
        businessKey=business_key,
        displayName=display_name,
        tenantId=tenant_id if tenant_id is not None else (existing.tenantId if existing else None),
        industry=industry if industry is not None else (existing.industry if existing else None),
        status=status,
        platforms=platforms,
        createdAt=existing.createdAt if existing else None,
    )


def apply_onboarding(
    manifest: ClientManifest,
    platform_inputs: list[PlatformInput],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    saved = upsert_client_manifest(manifest, dry_run=dry_run)
    settings = get_settings()
    secrets_written: list[str] = []
    for pi in platform_inputs:
        if not pi.enabled or not pi.credentials:
            continue
        sid = credential_secret_id(manifest.businessKey, pi.platform)
        existing_secret = secrets_mod.get_secret(sid, settings) if not dry_run else None
        merged = dict(existing_secret) if isinstance(existing_secret, dict) else {}
        merged.update({k: v for k, v in pi.credentials.items() if v})
        merged.update({k: v for k, v in pi.metadata.items() if v})
        if not dry_run:
            secrets_mod.put_secret(sid, merged, settings)
        secrets_written.append(sid)
    return {
        "manifest": saved.public_dict(),
        "secretsWritten": secrets_written,
        "dryRun": dry_run,
    }


def validate_platform(business_key: str, platform: str) -> tuple[bool, str]:
    try:
        if platform == "google-ads":
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "servers" / "google-ads"))
            from tools.read import list_accounts  # type: ignore
            from shared.models import ToolRequest

            resp = list_accounts(ToolRequest(businessKey=business_key), request_id=None)
            return bool(resp.get("ok")), resp.get("summary") or "ok"
        # Other platforms: presence check only for now.
        from shared.manifest import client_readiness

        report = client_readiness(business_key)
        if report is None:
            return False, "no manifest"
        pr = report.platforms[platform]
        return pr.ready, ("ready" if pr.ready else f"missing: {pr.missingKeys}")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/scripts/test_onboard_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/_onboard_core.py tests/scripts/test_onboard_core.py
git commit -m "feat(onboard): shared onboarding core module"
```

---

## Task 7: `scripts/onboard-client.py` CLI

**Files:**
- Create: `scripts/onboard-client.py`
- Test: manual (documented below) + one smoke test in `tests/scripts/test_onboard_core.py` is enough; CLI arg-parsing gets a lightweight test here.
- Test: `tests/scripts/test_onboard_cli.py`

**Interfaces:**
- Consumes: `scripts._onboard_core`, `shared.manifest`.
- Produces: CLI with subcommands `add`, `update`, `show`, `list`, `enable`, `disable`, `validate`. Common flags `--dry-run`, `--region`, `--from-file`, `--key`, `--name`, `--tenant`, `--industry`, `--platform`, `--yes` (skip prompts — requires `--from-file` or all `--platform ...:key=val` specs), `--skip-validate`.
- Exit codes: `0` ok, `1` validation failure, `2` usage error.

- [ ] **Step 1: Write the failing test** — `tests/scripts/test_onboard_cli.py`

```python
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

CLI = Path(__file__).resolve().parents[2] / "scripts" / "onboard-client.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("onboard_client", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_list_empty(capsys, clients_dir, fake_secrets):
    cli = _load_cli()
    rc = cli.main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No clients" in out or out.strip() == "[]"


def test_add_from_file_dry_run(tmp_path, capsys, clients_dir, fake_secrets):
    flat = {"acme": {"customer_account_id": "111", "developer_token": "d",
                     "client_id": "c", "client_secret": "s", "refresh_token": "r"}}
    f = tmp_path / "flat.json"
    f.write_text(json.dumps(flat))
    cli = _load_cli()
    rc = cli.main(["add", "--key", "acme", "--name", "Acme",
                   "--from-file", str(f), "--yes", "--dry-run", "--skip-validate"])
    assert rc == 0
    assert fake_secrets.store == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scripts/test_onboard_cli.py -v`
Expected: FAIL — file not found.

- [ ] **Step 3: Implement `scripts/onboard-client.py`**

```python
#!/usr/bin/env python3
"""Onboard or update a marketing client with any subset of the five platforms.

Examples:
    python scripts/onboard-client.py add --key acme --name "Acme Co" --tenant 12
    python scripts/onboard-client.py add --key acme --name "Acme" --from-file local-dev-config.json --yes
    python scripts/onboard-client.py show --key acme
    python scripts/onboard-client.py list
    python scripts/onboard-client.py enable --key acme --platform gbp
    python scripts/onboard-client.py validate --key acme
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _onboard_core as core  # noqa: E402
from shared import manifest as manifest_mod  # noqa: E402
from shared.errors import AdsMcpError  # noqa: E402
from shared.models import ClientManifest  # noqa: E402

PLATFORMS = core.KNOWN_PLATFORMS if hasattr(core, "KNOWN_PLATFORMS") else manifest_mod.KNOWN_PLATFORMS


def _prompt(label: str, *, secret: bool = False, default: str = "") -> str:
    import getpass

    suffix = f" [{default}]" if default else ""
    raw = (getpass.getpass if secret else input)(f"{label}{suffix}: ").strip()
    return raw or default


def _collect_interactive(existing: ClientManifest | None) -> list[core.PlatformInput]:
    inputs: list[core.PlatformInput] = []
    for platform in manifest_mod.KNOWN_PLATFORMS:
        cur = existing.platforms.get(platform) if existing else None
        default_enabled = "y" if (cur and cur.enabled) else "n"
        ans = _prompt(f"Enable {platform}? (y/n)", default=default_enabled).lower()
        enabled = ans.startswith("y")
        if not enabled:
            inputs.append(core.PlatformInput(platform, False, {}, {}))
            continue
        metadata = {}
        for key in core.METADATA_KEYS_BY_PLATFORM.get(platform, ()):
            metadata[key] = _prompt(f"  {platform}.{key}")
        credentials = {}
        for key in core.CREDENTIAL_KEYS_BY_PLATFORM.get(platform, ()):
            credentials[key] = _prompt(f"  {platform}.{key}", secret=True)
        inputs.append(core.PlatformInput(platform, True, metadata, credentials))
    return inputs


def _region_from_args(args) -> None:
    if getattr(args, "region", None):
        os.environ["AWS_REGION"] = args.region
        from shared.config import get_settings

        get_settings.cache_clear()


def cmd_add(args) -> int:
    _region_from_args(args)
    existing = manifest_mod.load_client_manifest(args.key)
    if args.from_file:
        flat_all = json.loads(Path(args.from_file).read_text())
        flat = flat_all.get(args.key, flat_all)
        inputs = core.derive_platform_inputs_from_flat(flat)
    elif args.yes:
        print("--yes requires --from-file", file=sys.stderr)
        return 2
    else:
        inputs = _collect_interactive(existing)

    manifest = core.build_manifest(
        args.key, args.name or (existing.displayName if existing else args.key),
        tenant_id=args.tenant, industry=args.industry,
        platform_inputs=inputs, existing=existing,
    )
    result = core.apply_onboarding(manifest, inputs, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))

    rc = 0
    if not args.dry_run and not args.skip_validate:
        for pi in inputs:
            if pi.enabled:
                ok, detail = core.validate_platform(args.key, pi.platform)
                print(f"  validate {pi.platform}: {'OK' if ok else 'FAIL'} — {detail}")
                rc = rc or (0 if ok else 1)

    _print_checklist(args.key)
    return rc


def cmd_update(args) -> int:
    return cmd_add(args)


def cmd_show(args) -> int:
    _region_from_args(args)
    report = manifest_mod.client_readiness(args.key)
    if report is None:
        print(f"No manifest for '{args.key}'.", file=sys.stderr)
        return 1
    print(json.dumps(report.model_dump(), indent=2))
    return 0


def cmd_list(args) -> int:
    _region_from_args(args)
    manifests = manifest_mod.list_client_manifests(include_inactive=True)
    if not manifests:
        print("No clients configured.")
        return 0
    for m in manifests:
        enabled = [p for p, c in m.platforms.items() if c.enabled]
        print(f"{m.businessKey:24} {m.status:9} {', '.join(enabled) or '(no platforms)'}")
    return 0


def cmd_enable(args) -> int:
    return _toggle(args, enabled=True)


def cmd_disable(args) -> int:
    return _toggle(args, enabled=False)


def _toggle(args, *, enabled: bool) -> int:
    _region_from_args(args)
    metadata = {}
    credentials = {}
    if enabled:
        for key in core.METADATA_KEYS_BY_PLATFORM.get(args.platform, ()):
            metadata[key] = _prompt(f"{args.platform}.{key}")
        for key in core.CREDENTIAL_KEYS_BY_PLATFORM.get(args.platform, ()):
            credentials[key] = _prompt(f"{args.platform}.{key}", secret=True)
    try:
        manifest_mod.set_platform_config(
            args.key, args.platform, enabled=enabled, metadata=metadata, dry_run=args.dry_run
        )
    except AdsMcpError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    if enabled and credentials and not args.dry_run:
        from shared.config import get_settings
        from shared import secrets as secrets_mod

        settings = get_settings()
        sid = manifest_mod.credential_secret_id(args.key, args.platform)
        cur = secrets_mod.get_secret(sid, settings)
        merged = dict(cur) if isinstance(cur, dict) else {}
        merged.update({k: v for k, v in {**credentials, **metadata}.items() if v})
        secrets_mod.put_secret(sid, merged, settings)
    print(f"{args.platform} {'enabled' if enabled else 'disabled'} for {args.key}.")
    return 0


def cmd_validate(args) -> int:
    _region_from_args(args)
    manifest = manifest_mod.load_client_manifest(args.key)
    if manifest is None:
        print(f"No manifest for '{args.key}'.", file=sys.stderr)
        return 1
    rc = 0
    targets = [args.platform] if args.platform else [
        p for p, c in manifest.platforms.items() if c.enabled
    ]
    for platform in targets:
        ok, detail = core.validate_platform(args.key, platform)
        print(f"{platform}: {'OK' if ok else 'FAIL'} — {detail}")
        rc = rc or (0 if ok else 1)
    return rc


def _print_checklist(key: str) -> None:
    print(
        f"\nNext steps for '{key}':\n"
        f"  1. If this client needs protected campaigns / geo locks / approval gates,\n"
        f"     add an entry to shared/rules.py GOOGLE_ADS_RULES['{key}'].\n"
        f"  2. If the content agent will write for this client, create\n"
        f"     servers/content-agent/brands/{key}.md from brands/_TEMPLATE.md.\n"
        f"  3. Add a business section to CLAUDE.md.\n"
        f"  4. Deploy:  bash scripts/deploy.sh\n"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--key", required=True)
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--region")

    a = sub.add_parser("add"); common(a)
    a.add_argument("--name"); a.add_argument("--tenant", type=int)
    a.add_argument("--industry"); a.add_argument("--from-file")
    a.add_argument("--yes", action="store_true"); a.add_argument("--skip-validate", action="store_true")
    a.set_defaults(func=cmd_add)

    u = sub.add_parser("update"); common(u)
    u.add_argument("--name"); u.add_argument("--tenant", type=int)
    u.add_argument("--industry"); u.add_argument("--from-file")
    u.add_argument("--yes", action="store_true"); u.add_argument("--skip-validate", action="store_true")
    u.set_defaults(func=cmd_update)

    s = sub.add_parser("show"); common(s); s.set_defaults(func=cmd_show)
    lst = sub.add_parser("list"); lst.add_argument("--region"); lst.set_defaults(func=cmd_list)

    for name, fn in (("enable", cmd_enable), ("disable", cmd_disable)):
        e = sub.add_parser(name); common(e)
        e.add_argument("--platform", required=True, choices=list(manifest_mod.KNOWN_PLATFORMS))
        e.set_defaults(func=fn)

    v = sub.add_parser("validate"); common(v)
    v.add_argument("--platform", choices=list(manifest_mod.KNOWN_PLATFORMS))
    v.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/scripts/ -v`
Expected: PASS

- [ ] **Step 5: Manual smoke (document result in commit body)**

Run: `python scripts/onboard-client.py list`
Expected: prints existing clients or "No clients configured."

- [ ] **Step 6: Commit**

```bash
git add scripts/onboard-client.py tests/scripts/test_onboard_cli.py
git commit -m "feat(onboard): onboard-client.py CLI with subcommands"
```

---

## Task 8: Refactor `push-secrets.py` onto the core

**Files:**
- Modify: `scripts/push-secrets.py`
- Test: `tests/scripts/test_push_secrets.py`

**Interfaces:**
- Consumes: `scripts._onboard_core.derive_platform_inputs_from_flat`, `build_manifest`, `apply_onboarding`.
- Produces: same CLI (`python scripts/push-secrets.py [--dry-run]`), same "read `local-dev-config.json`" behavior — but now: (a) writes a manifest per business, (b) writes a `google-ads` credential secret **only when** `customer_account_id` is present, (c) keeps the existing skip messages for analytics / search-console / gbp.

- [ ] **Step 1: Write the failing test** — `tests/scripts/test_push_secrets.py`

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import _onboard_core as core
from shared import manifest as manifest_mod


def test_no_google_ads_secret_when_customer_id_absent(clients_dir, fake_secrets):
    flat = {"acme": {"site_url": "https://a.com", "client_id": "c",
                     "client_secret": "s", "refresh_token": "r"}}
    for key, obj in flat.items():
        inputs = core.derive_platform_inputs_from_flat(obj)
        manifest = core.build_manifest(key, key.title(), platform_inputs=inputs)
        core.apply_onboarding(manifest, inputs)
    assert manifest_mod.credential_secret_id("acme", "google-ads") not in fake_secrets.store
    assert manifest_mod.credential_secret_id("acme", "search-console") in fake_secrets.store
    assert fake_secrets.store[manifest_mod.manifest_secret_id("acme")]["platforms"]["google-ads"]["enabled"] is False
```

(This test validates the core path `push-secrets.py` now delegates to; the CLI wrapper is covered by the existing `--dry-run` manual run.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scripts/test_push_secrets.py -v`
Expected: FAIL if run before Task 6 merged; PASS-ready after. If it passes already (Task 6 done), proceed — the remaining work is wiring the CLI.

- [ ] **Step 3: Rewrite `scripts/push-secrets.py` `main()`**

Keep the module docstring and `CONFIG_FILE` resolution. Replace `aws_put_secret` + the per-business loop with:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="Push credentials to AWS Secrets Manager")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from scripts import _onboard_core as core

    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found.", file=sys.stderr)
        sys.exit(1)

    config = json.loads(CONFIG_FILE.read_text())
    for business_key, creds in config.items():
        if not isinstance(creds, dict):
            continue
        print(f"Business: {business_key}")
        inputs = core.derive_platform_inputs_from_flat(creds)
        manifest = core.build_manifest(
            business_key,
            creds.get("display_name", business_key),
            platform_inputs=inputs,
        )
        result = core.apply_onboarding(manifest, inputs, dry_run=args.dry_run)
        for pi in inputs:
            state = "enabled" if pi.enabled else "SKIP (not configured)"
            print(f"  {pi.platform}: {state}")
        for sid in result["secretsWritten"]:
            print(f"    {'[DRY RUN] would write' if args.dry_run else 'wrote'}: {sid}")
        print()
    print("Done.")
```

Remove now-unused `subprocess` / `aws_put_secret` / `REGION` if nothing else uses them (leave `REGION` if referenced elsewhere — it is not).

- [ ] **Step 4: Run tests + manual dry run**

Run: `pytest tests/scripts/ -v`
Run: `python scripts/push-secrets.py --dry-run` (uses real `local-dev-config.json`)
Expected: tests pass; dry-run prints per-platform enabled/skip + would-write lines, no `google-ads` secret line for a business without `customer_account_id`.

- [ ] **Step 5: Commit**

```bash
git add scripts/push-secrets.py tests/scripts/test_push_secrets.py
git commit -m "refactor(push-secrets): delegate to onboarding core; no empty google-ads secret"
```

---

## Task 9: Backfill script + committed dev manifests

**Files:**
- Create: `scripts/backfill-manifests.py`
- Create: `clients/rnr-electrician.json`
- Create: `clients/gq-painting.json`
- Modify: `.gitignore` (confirm `clients/` is NOT ignored; add `!clients/` guard if a broad rule catches it)
- Test: `tests/scripts/test_backfill.py`

**Interfaces:**
- Consumes: `scripts._onboard_core`, `shared.manifest`, `shared.secrets` (read existing credential secrets), `shared.config.get_settings`.
- Produces: `backfill_from_flat(flat_config: dict, *, dry_run: bool) -> list[dict]` — for each business key, build + write a manifest whose platform `enabled` flags are set from **which credential secrets already exist in the store** (falling back to flat-config presence when a secret can't be read); returns per-business result dicts. CLI: `python scripts/backfill-manifests.py [--dry-run] [--from-file PATH]`.

- [ ] **Step 1: Write the failing test** — `tests/scripts/test_backfill.py`

```python
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import backfill_manifests as bf  # note: import via importlib in impl if hyphenated
from shared import manifest as manifest_mod


def test_backfill_enables_platform_when_secret_exists(clients_dir, fake_secrets):
    fake_secrets.store[manifest_mod.credential_secret_id("acme", "google-ads")] = {
        "customer_account_id": "111", "developer_token": "d",
        "client_id": "c", "client_secret": "s", "refresh_token": "r",
    }
    flat = {"acme": {"customer_account_id": "111"}}
    bf.backfill_from_flat(flat, dry_run=False)
    manifest = manifest_mod.load_client_manifest("acme")
    assert manifest.platforms["google-ads"].enabled is True
    assert manifest.platforms["analytics"].enabled is False
    assert "acme" in fake_secrets.store[manifest_mod.INDEX_SECRET_ID]
```

(If Python import of a hyphenated filename is awkward, name the module `scripts/backfill_manifests.py` and make `backfill-manifests.py` unnecessary — OR load via importlib in the test. Choose `scripts/backfill_manifests.py` for importability and add a `# invoked as: python scripts/backfill_manifests.py` note. Update the File Structure entry accordingly.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scripts/test_backfill.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `scripts/backfill_manifests.py`**

```python
#!/usr/bin/env python3
"""One-time: derive client manifests from existing credential secrets.

Usage:
    python scripts/backfill_manifests.py --dry-run
    python scripts/backfill_manifests.py --from-file local-dev-config.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _onboard_core as core  # noqa: E402
from shared import manifest as manifest_mod  # noqa: E402
from shared import secrets as secrets_mod  # noqa: E402
from shared.config import get_settings  # noqa: E402


def _platform_secret_exists(business_key: str, platform: str) -> bool:
    settings = get_settings()
    val = secrets_mod.get_secret(
        manifest_mod.credential_secret_id(business_key, platform), settings
    )
    return isinstance(val, dict) and bool(val)


def backfill_from_flat(flat_config: dict, *, dry_run: bool) -> list[dict]:
    results: list[dict] = []
    for business_key, creds in flat_config.items():
        if not isinstance(creds, dict):
            continue
        inputs = core.derive_platform_inputs_from_flat(creds)
        for pi in inputs:
            if _platform_secret_exists(business_key, pi.platform):
                pi.enabled = True
        existing = manifest_mod.load_client_manifest(business_key)
        manifest = core.build_manifest(
            business_key, creds.get("display_name", business_key),
            platform_inputs=inputs, existing=existing,
        )
        # Backfill must not overwrite credential secrets — only the manifest.
        saved = manifest_mod.upsert_client_manifest(manifest, dry_run=dry_run)
        results.append({"businessKey": business_key,
                        "enabled": [p for p, c in saved.platforms.items() if c.enabled],
                        "dryRun": dry_run})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-file", default=str(ROOT / "local-dev-config.json"))
    args = parser.parse_args(argv)

    flat = json.loads(Path(args.from_file).read_text())
    for row in backfill_from_flat(flat, dry_run=args.dry_run):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write the committed dev manifests**

`clients/rnr-electrician.json` (metadata only — values from `CLAUDE.md` + `local-dev-config.example.jsonc`; **confirm enabled set with owner** — spec open question):

```json
{
  "businessKey": "rnr-electrician",
  "displayName": "RnR Electrician",
  "industry": "Electrician / home services",
  "status": "active",
  "platforms": {
    "google-ads":     { "enabled": true,  "customer_account_id": "1057140994", "manager_account_id": "4746289774" },
    "analytics":      { "enabled": false },
    "search-console": { "enabled": true,  "site_url": "https://www.rnrelectrician.com" },
    "gbp":            { "enabled": false },
    "meta-ads":       { "enabled": false }
  }
}
```

`clients/gq-painting.json`:

```json
{
  "businessKey": "gq-painting",
  "displayName": "GQ Custom Painting",
  "industry": "Painting contractor",
  "status": "active",
  "platforms": {
    "google-ads":     { "enabled": true,  "customer_account_id": "7586427009", "manager_account_id": "4746289774" },
    "analytics":      { "enabled": false },
    "search-console": { "enabled": true,  "site_url": "https://www.gilqpaiting.com/" },
    "gbp":            { "enabled": false },
    "meta-ads":       { "enabled": false }
  }
}
```

- [ ] **Step 5: Check `.gitignore`**

Run: `git check-ignore clients/rnr-electrician.json` → expect **no output** (not ignored). If ignored, add `!clients/` and `!clients/*.json` lines.

- [ ] **Step 6: Run tests + full suite**

Run: `pytest -q`
Expected: all pass. Note: adding `clients/*.json` means `load_client_manifest("rnr-electrician")` now returns non-None in any environment with the repo checked out — the `test_runtime_config_manifest.py` tests use `clients_dir` fixture (tmp dir) so they are unaffected.

- [ ] **Step 7: Commit**

```bash
git add scripts/backfill_manifests.py clients/ tests/scripts/test_backfill.py .gitignore
git commit -m "feat(onboard): backfill script + committed dev manifests"
```

---

## Task 10: Signature gate covers `/admin/`

**Files:**
- Modify: `shared/auth.py:123`
- Test: `tests/shared/test_auth_admin_paths.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SignedRequestMiddleware.dispatch` verifies signed requests when `require_signed_requests` and the path starts with `/tools/` **or** `/admin/`.

- [ ] **Step 1: Write the failing test** — `tests/shared/test_auth_admin_paths.py`

```python
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from shared.auth import SignedRequestMiddleware
from shared.config import Settings


def _app(require: bool):
    async def ok(request):
        return JSONResponse({"ok": True})

    settings = Settings(
        require_signed_requests=require,
        auth_timestamp_tolerance_seconds=300,
        auth_nonce_ttl_seconds=600,
        auth_signing_secret_prefix="x",
        auth_signing_keys_json=None,
        redis_url=None,
        aws_region="us-east-1",
        secret_cache_ttl_seconds=300,
    )
    app = Starlette(routes=[
        Route("/admin/clients", ok, methods=["POST"]),
        Route("/health", ok),
    ])
    app.add_middleware(SignedRequestMiddleware, service_name="orchestrator", settings=settings)
    return app


def test_admin_requires_signature_when_enabled():
    client = TestClient(_app(True))
    resp = client.post("/admin/clients", json={})
    assert resp.status_code == 401


def test_admin_open_when_signing_disabled():
    client = TestClient(_app(False))
    assert client.post("/admin/clients", json={}).status_code == 200


def test_health_never_gated():
    assert TestClient(_app(True)).get("/health").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/shared/test_auth_admin_paths.py -v`
Expected: FAIL — `test_admin_requires_signature_when_enabled` gets 200.

- [ ] **Step 3: Implement — `shared/auth.py`**

Change line ~123:

```python
        gated = request.url.path.startswith(("/tools/", "/admin/"))
        if self.settings.require_signed_requests and gated:
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/shared/test_auth_admin_paths.py -v`
Expected: PASS (3)

- [ ] **Step 5: Commit**

```bash
git add shared/auth.py tests/shared/test_auth_admin_paths.py
git commit -m "feat(auth): gate /admin/ paths with request signing"
```

---

## Task 11: Admin router on the orchestrator

**Files:**
- Create: `servers/orchestrator/admin.py`
- Modify: `servers/orchestrator/main.py`
- Test: `tests/servers/test_orchestrator_admin.py`

**Interfaces:**
- Consumes: `shared.manifest` (load/list/readiness/set_platform_config/delete), `scripts._onboard_core` (build_manifest, apply_onboarding, PlatformInput, validate_platform), `shared.models.ClientManifest`, `shared.errors.AdsMcpError`.
- Produces: `router: APIRouter` with:

| Method + path | Request body (Pydantic) | Response |
| --- | --- | --- |
| `POST /admin/clients` | `UpsertClientBody{ businessKey, displayName, tenantId?, industry?, status?, platforms: dict[str, PlatformInputBody], dryRun? }` | `{ ok, mode, manifest, readiness, secretsWritten }` |
| `GET /admin/clients` | – (`?includeInactive=`) | `{ ok, clients: [readiness...] }` |
| `GET /admin/clients/{businessKey}` | – | `{ ok, manifest, readiness }` (404 if none) |
| `PUT /admin/clients/{businessKey}/platforms/{platform}` | `PlatformUpsertBody{ enabled, metadata?, credentials?, dryRun? }` | `{ ok, mode, manifest, readiness }` |
| `POST /admin/clients/{businessKey}/platforms/{platform}/validate` | – | `{ ok, platform, ready, detail }` |
| `DELETE /admin/clients/{businessKey}/platforms/{platform}` | – | `{ ok, manifest, readiness }` |

- `PlatformInputBody{ enabled: bool, metadata: dict[str,str] = {}, credentials: dict[str,str] = {} }`.
- **No response ever includes credential values.** Only `manifest` (from `ClientManifest.public_dict()`, which holds metadata only) and `readiness` (`ReadinessReport.model_dump()`).

- [ ] **Step 1: Write the failing tests** — `tests/servers/test_orchestrator_admin.py`

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "servers" / "orchestrator"))

from admin import router  # noqa: E402
from shared import manifest as manifest_mod  # noqa: E402


@pytest.fixture
def client(clients_dir, fake_secrets):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


SECRET_KEY_NAMES = {"client_secret", "refresh_token", "developer_token", "access_token"}


def _assert_no_secrets(payload):
    text = str(payload)
    for name in SECRET_KEY_NAMES:
        assert name not in text, f"secret key {name} leaked in response"


def test_upsert_then_get_roundtrip(client):
    body = {
        "businessKey": "acme", "displayName": "Acme Co",
        "platforms": {
            "search-console": {
                "enabled": True, "metadata": {"site_url": "https://a.com"},
                "credentials": {"client_id": "c", "client_secret": "s", "refresh_token": "r"},
            }
        },
    }
    r = client.post("/admin/clients", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] and data["mode"] == "execute"
    assert data["readiness"]["platforms"]["search-console"]["ready"] is True
    _assert_no_secrets(data)

    g = client.get("/admin/clients/acme")
    assert g.status_code == 200
    _assert_no_secrets(g.json())
    assert g.json()["manifest"]["platforms"]["search-console"]["site_url"] == "https://a.com"


def test_upsert_dry_run_writes_nothing(client, fake_secrets):
    body = {"businessKey": "acme", "displayName": "Acme", "platforms": {}, "dryRun": True}
    r = client.post("/admin/clients", json=body)
    assert r.json()["mode"] == "dry-run"
    assert fake_secrets.store == {}


def test_get_unknown_is_404(client):
    assert client.get("/admin/clients/ghost").status_code == 404


def test_put_platform_disable(client):
    client.post("/admin/clients", json={
        "businessKey": "acme", "displayName": "Acme",
        "platforms": {"gbp": {"enabled": True, "metadata": {"gbp_location_id": "loc"},
                              "credentials": {"client_id": "c", "client_secret": "s", "refresh_token": "r"}}},
    })
    r = client.request("DELETE", "/admin/clients/acme/platforms/gbp")
    assert r.status_code == 200
    assert r.json()["manifest"]["platforms"]["gbp"]["enabled"] is False


def test_list_clients(client):
    client.post("/admin/clients", json={"businessKey": "acme", "displayName": "Acme", "platforms": {}})
    r = client.get("/admin/clients")
    assert r.status_code == 200
    assert any(c["businessKey"] == "acme" for c in r.json()["clients"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/servers/test_orchestrator_admin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin'`

- [ ] **Step 3: Implement `servers/orchestrator/admin.py`**

```python
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from scripts import _onboard_core as core
from shared import manifest as manifest_mod
from shared.errors import AdsMcpError

router = APIRouter(prefix="/admin", tags=["admin"])


class PlatformInputBody(BaseModel):
    enabled: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)


class UpsertClientBody(BaseModel):
    businessKey: str
    displayName: str
    tenantId: int | None = None
    industry: str | None = None
    status: str = "active"
    platforms: dict[str, PlatformInputBody] = Field(default_factory=dict)
    dryRun: bool = False


class PlatformUpsertBody(BaseModel):
    enabled: bool
    metadata: dict[str, str] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    dryRun: bool = False


def _readiness_dict(business_key: str) -> dict:
    report = manifest_mod.client_readiness(business_key)
    return report.model_dump() if report else {}


def _inputs_from_bodies(bodies: dict[str, PlatformInputBody]) -> list[core.PlatformInput]:
    out: list[core.PlatformInput] = []
    for platform, body in bodies.items():
        if platform not in manifest_mod.KNOWN_PLATFORMS:
            raise HTTPException(status_code=400, detail=f"Unknown platform '{platform}'.")
        out.append(core.PlatformInput(platform, body.enabled, dict(body.metadata), dict(body.credentials)))
    return out


@router.post("/clients")
def upsert_client(body: UpsertClientBody) -> dict:
    existing = manifest_mod.load_client_manifest(body.businessKey)
    inputs = _inputs_from_bodies(body.platforms)
    manifest = core.build_manifest(
        body.businessKey, body.displayName,
        tenant_id=body.tenantId, industry=body.industry, status=body.status,
        platform_inputs=inputs, existing=existing,
    )
    try:
        result = core.apply_onboarding(manifest, inputs, dry_run=body.dryRun)
    except AdsMcpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {
        "ok": True,
        "mode": "dry-run" if body.dryRun else "execute",
        "manifest": result["manifest"],
        "secretsWritten": result["secretsWritten"],
        "readiness": {} if body.dryRun else _readiness_dict(body.businessKey),
    }


@router.get("/clients")
def list_clients(includeInactive: bool = Query(False)) -> dict:
    manifests = manifest_mod.list_client_manifests(include_inactive=includeInactive)
    return {"ok": True, "clients": [_readiness_dict(m.businessKey) for m in manifests]}


@router.get("/clients/{business_key}")
def get_client(business_key: str) -> dict:
    manifest = manifest_mod.load_client_manifest(business_key)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"No manifest for '{business_key}'.")
    return {"ok": True, "manifest": manifest.public_dict(), "readiness": _readiness_dict(business_key)}


@router.put("/clients/{business_key}/platforms/{platform}")
def put_platform(business_key: str, platform: str, body: PlatformUpsertBody) -> dict:
    try:
        manifest = manifest_mod.set_platform_config(
            business_key, platform, enabled=body.enabled,
            metadata=dict(body.metadata), dry_run=body.dryRun,
        )
    except AdsMcpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    if body.enabled and body.credentials and not body.dryRun:
        from shared import secrets as secrets_mod
        from shared.config import get_settings

        settings = get_settings()
        sid = manifest_mod.credential_secret_id(business_key, platform)
        cur = secrets_mod.get_secret(sid, settings)
        merged = dict(cur) if isinstance(cur, dict) else {}
        merged.update({k: v for k, v in {**body.credentials, **body.metadata}.items() if v})
        secrets_mod.put_secret(sid, merged, settings)

    return {
        "ok": True,
        "mode": "dry-run" if body.dryRun else "execute",
        "manifest": manifest.public_dict(),
        "readiness": {} if body.dryRun else _readiness_dict(business_key),
    }


@router.post("/clients/{business_key}/platforms/{platform}/validate")
def validate(business_key: str, platform: str) -> dict:
    ok, detail = core.validate_platform(business_key, platform)
    return {"ok": ok, "platform": platform, "ready": ok, "detail": detail}


@router.delete("/clients/{business_key}/platforms/{platform}")
def disable_platform(business_key: str, platform: str) -> dict:
    try:
        manifest = manifest_mod.set_platform_config(business_key, platform, enabled=False)
    except AdsMcpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True, "manifest": manifest.public_dict(), "readiness": _readiness_dict(business_key)}
```

- [ ] **Step 4: Wire into `servers/orchestrator/main.py`**

After `app.add_middleware(...)`:

```python
from admin import router as admin_router

app.include_router(admin_router)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/servers/test_orchestrator_admin.py -v`
Expected: PASS (5)

- [ ] **Step 6: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add servers/orchestrator/admin.py servers/orchestrator/main.py tests/servers/test_orchestrator_admin.py
git commit -m "feat(orchestrator): signed /admin/clients onboarding endpoints"
```

---

## Task 12: MCP discovery tools

**Files:**
- Modify: `servers/orchestrator/mcp_server.py`
- Test: `tests/servers/test_orchestrator_discovery.py`

**Interfaces:**
- Consumes: `shared.manifest.list_client_manifests`, `client_readiness`.
- Produces: two FastMCP tools:
  - `list_clients() -> dict` → `{ "clients": [ {businessKey, displayName, status, platforms:{p:{enabled,ready,missingKeys}}} ... ] }` (active only).
  - `get_client_status(business_key: str) -> dict` → readiness report dict, or `{ "error": "..." }` if no manifest.
  - No credentials in either.

- [ ] **Step 1: Write the failing test** — `tests/servers/test_orchestrator_discovery.py`

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "servers" / "orchestrator"))


@pytest.fixture
def discovery(clients_dir, fake_secrets):
    import importlib
    import mcp_server
    importlib.reload(mcp_server)
    return mcp_server


def test_list_clients_returns_readiness(discovery, clients_dir, fake_secrets):
    import json
    (clients_dir / "acme.json").write_text(json.dumps({
        "businessKey": "acme", "displayName": "Acme",
        "platforms": {"google-ads": {"enabled": True}},
    }))
    result = discovery._list_clients_impl()
    row = next(c for c in result["clients"] if c["businessKey"] == "acme")
    assert row["platforms"]["google-ads"]["enabled"] is True
    assert "client_secret" not in str(result)


def test_get_client_status_unknown(discovery):
    assert "error" in discovery._get_client_status_impl("ghost")
```

Note: expose thin `_list_clients_impl()` / `_get_client_status_impl(key)` free functions so tests don't need the FastMCP runtime; the `@mcp.tool` functions just call them.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/servers/test_orchestrator_discovery.py -v`
Expected: FAIL — `_list_clients_impl` missing.

- [ ] **Step 3: Implement — add to `servers/orchestrator/mcp_server.py`**

```python
from shared import manifest as manifest_mod


def _list_clients_impl() -> dict:
    clients = []
    for m in manifest_mod.list_client_manifests():
        report = manifest_mod.client_readiness(m.businessKey)
        clients.append(report.model_dump() if report else {
            "businessKey": m.businessKey, "displayName": m.displayName,
            "status": m.status, "platforms": {},
        })
    return {"clients": clients}


def _get_client_status_impl(business_key: str) -> dict:
    report = manifest_mod.client_readiness(business_key)
    if report is None:
        return {"error": f"No manifest for '{business_key}'."}
    return report.model_dump()


@mcp.tool(
    description="List all onboarded marketing clients and which platforms are enabled/ready. No credentials returned."
)
def list_clients() -> dict:
    return _list_clients_impl()


@mcp.tool(
    description="Get platform readiness for one client by business key."
)
def get_client_status(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician'")],
) -> dict:
    return _get_client_status_impl(business_key)
```

(Match the existing import style in that file for `Annotated` / `Field` / `mcp`.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/servers/test_orchestrator_discovery.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add servers/orchestrator/mcp_server.py tests/servers/test_orchestrator_discovery.py
git commit -m "feat(orchestrator): list_clients + get_client_status MCP tools"
```

---

## Task 13: Content-agent brand-file warning + template

**Files:**
- Create: `servers/content-agent/brands/_TEMPLATE.md`
- Modify: `servers/content-agent/mcp_server.py`
- Test: `tests/servers/test_content_agent_brand_warning.py`

**Interfaces:**
- Consumes: `shared.manifest.load_client_manifest`.
- Produces: when `brands/{business_key}.md` is missing **and** a manifest exists, the `write_google_ad` response includes `warnings: ["No brand file for {key}; using generic voice. Create servers/content-agent/brands/{key}.md from _TEMPLATE.md."]`. When the brand file exists, no such warning. `_TEMPLATE.md` exists.

- [ ] **Step 1: Write `_TEMPLATE.md`**

```markdown
# <Display Name> — Brand Voice

> Copy this file to `servers/content-agent/brands/<business-key>.md` and fill it in.

## Business
- Industry:
- Service area (cities):
- Website:

## Voice & tone
- (e.g. friendly, direct, local, no jargon)

## Always mention
- (licensing, guarantees, response time…)

## Never say
- (claims you can't back, competitor names…)

## Service priorities
- (equal priority vs. lead service)
```

- [ ] **Step 2: Write the failing test** — `tests/servers/test_content_agent_brand_warning.py`

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "servers" / "content-agent"))


@pytest.fixture
def agent(clients_dir, fake_secrets):
    import importlib
    import mcp_server
    importlib.reload(mcp_server)
    return mcp_server


def test_warning_when_brand_missing_but_manifest_exists(agent, clients_dir):
    (clients_dir / "acme.json").write_text(json.dumps(
        {"businessKey": "acme", "displayName": "Acme", "platforms": {}}
    ))
    resp = agent.write_google_ad.fn("acme", keyword="electrician", city="LA")
    assert any("No brand file" in w for w in resp.get("warnings", []))


def test_no_warning_for_existing_brand(agent):
    resp = agent.write_google_ad.fn("rnr-electrician", keyword="electrician", city="LA")
    assert not any("No brand file" in w for w in resp.get("warnings", []))
```

(If `write_google_ad` isn't a FastMCP `FunctionTool` with `.fn`, call the underlying function directly — match what the file exposes.)

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/servers/test_content_agent_brand_warning.py -v`
Expected: FAIL — no `warnings` key.

- [ ] **Step 4: Implement — `servers/content-agent/mcp_server.py`**

In `write_google_ad`, after `brand_context = ...`:

```python
    warnings: list[str] = []
    if not brand_context:
        from shared import manifest as manifest_mod

        if manifest_mod.load_client_manifest(business_key) is not None:
            warnings.append(
                f"No brand file for {business_key}; using generic voice. "
                f"Create servers/content-agent/brands/{business_key}.md from _TEMPLATE.md."
            )
```

And pass `warnings=warnings` into `build_success_response(...)`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/servers/test_content_agent_brand_warning.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add servers/content-agent/brands/_TEMPLATE.md servers/content-agent/mcp_server.py tests/servers/test_content_agent_brand_warning.py
git commit -m "feat(content-agent): warn on missing brand file; add template"
```

---

## Task 14: Documentation

**Files:**
- Create: `docs/ONBOARDING.md`
- Create: `docs/CLIENT_MANIFEST.md`
- Modify: `docs/INTEGRATION_CONTRACT.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `scripts/signed_request_smoke.py` (default body helper for `/admin/clients`)

**Interfaces:** none (docs). The smoke-script change adds an `--admin` flag that swaps the default body to a manifest dry-run upsert.

- [ ] **Step 1: Write `docs/CLIENT_MANIFEST.md`**

Contents (write in full):
- What a manifest is; that it holds **no credentials**.
- The JSON schema — every field, types, the `status` enum.
- The five platform metadata shapes (table: platform → metadata keys → credential keys).
- Storage precedence: env `ADS_MCP_CLIENT_MANIFESTS_JSON` → `clients/{key}.json` → secret `/ads-mcp/{key}/manifest`.
- The `/ads-mcp/_index/clients` index secret and why it exists.
- "Usable" definition: `enabled: true` AND credential secret complete.
- Error codes `PLATFORM_NOT_CONFIGURED` (404) and `PLATFORM_CONFIG_INCOMPLETE` (409) with example JSON bodies.

- [ ] **Step 2: Write `docs/ONBOARDING.md`**

Contents (write in full):
1. **"A client can have any subset of the five platforms."**
2. **Platform table** — for each of `google-ads`, `analytics`, `search-console`, `gbp`, `meta-ads`: what IDs it needs, where to get them (Google Ads UI, GA4 Admin → Property Settings, Search Console property, `scripts/gbp-discover.py`, Meta Business Manager), OAuth scope, read-only vs write.
3. **Path A — CLI**:
   - `python scripts/onboard-client.py add --key <k> --name "<Name>" --tenant <id>` then answer the per-platform prompts.
   - Non-interactive: `--from-file local-dev-config.json --yes`.
   - Always `--dry-run` first; show sample output.
   - `python scripts/onboard-client.py validate --key <k>`.
   - `python scripts/onboard-client.py list` / `show --key <k>`.
   - Enable a platform later: `enable --key <k> --platform gbp`.
4. **Path B — admin dashboard / backend-rc**: the six `/admin/clients*` endpoints, with a signed `curl` example for `POST /admin/clients` (dry-run) and the JSON response. Reference `docs/INTEGRATION_CONTRACT.md` for the signing scheme.
5. **Post-onboarding checklist**: rules.py entry (with a paste-ready template block), brand file, `CLAUDE.md` section, `bash scripts/deploy.sh`.
6. **Migration note**: run `python scripts/backfill_manifests.py --dry-run` once for existing clients.
7. **Troubleshooting**: `PLATFORM_NOT_CONFIGURED` → platform disabled/absent in manifest; `PLATFORM_CONFIG_INCOMPLETE` → check `onboard-client.py show` `missingKeys`; validation failure → creds wrong, re-run `enable`; `list` missing a client → index secret out of sync, re-run `onboard-client.py add`.

Rules template block to include verbatim:

```python
# shared/rules.py — add inside GOOGLE_ADS_RULES
"<business-key>": {
    "protected_campaigns": set(),          # names that must never be modified
    "protected_ad_groups": set(),
    "lock_geo_changes": False,             # True to block geo edits
    "keyword_changes_require_explicit_approval": False,
},
```

- [ ] **Step 3: Update `docs/INTEGRATION_CONTRACT.md`**

Under "Minimum API shape between backend-rc and ads-mcp", add an "### Admin / onboarding endpoints" subsection listing the six routes, noting: signed like `/tools/`, `dryRun` supported on writes, credentials never returned, manifest ownership is `ads-mcp`.

- [ ] **Step 4: Update `README.md`**

Replace section "**3. Create your local credentials file**" body with:

```markdown
See **[docs/ONBOARDING.md](docs/ONBOARDING.md)** for how to onboard a client
with any subset of the five platforms. For local dev you still need a
`local-dev-config.json` (gitignored) — its shape is in
`local-dev-config.example.jsonc`. `scripts/push-secrets.py` now writes a
per-client manifest and only creates the platform secrets a client actually has.
```

Leave the rest of the README unchanged.

- [ ] **Step 5: Update `CLAUDE.md`**

- Under "## Build Order", add:
  ```
  13. Client onboarding (partial-platform) ✅
      - Per-client manifest (docs/CLIENT_MANIFEST.md); CLI + signed /admin/clients endpoints (docs/ONBOARDING.md)
  ```
- Under "## Standing Rules", add:
  ```
  9. The client manifest is the source of truth for which platforms a client has. A disabled/absent platform must return PLATFORM_NOT_CONFIGURED, never a generic 500.
  ```

- [ ] **Step 6: Update `scripts/signed_request_smoke.py`**

Add `--admin` flag; when set and no `--body-file`, `load_body` returns:

```python
json.dumps({
    "businessKey": "smoke-test-client",
    "displayName": "Smoke Test Client",
    "platforms": {},
    "dryRun": True,
}).encode("utf-8")
```

- [ ] **Step 7: Verify docs render + links resolve**

Run: `python -c "import pathlib; [print(p) for p in ['docs/ONBOARDING.md','docs/CLIENT_MANIFEST.md'] if not pathlib.Path(p).exists()]"`
Expected: no output (both exist).
Run: `pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add docs/ONBOARDING.md docs/CLIENT_MANIFEST.md docs/INTEGRATION_CONTRACT.md README.md CLAUDE.md scripts/signed_request_smoke.py
git commit -m "docs: onboarding guide, manifest reference, contract + smoke updates"
```

---

## Task 15: Full-suite regression + backfill dry-run

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -q`
Expected: all pass, no warnings about unclosed resources or import errors.

- [ ] **Step 2: Import every server entrypoint**

Run:
```bash
for s in google-ads meta-ads analytics search-console content-agent gbp orchestrator; do
  (cd "servers/$s" && python -c "import main" && echo "$s main OK") || echo "$s FAILED"
done
```
Expected: all `OK`.

- [ ] **Step 3: Backfill dry-run against real config**

Run: `python scripts/backfill_manifests.py --dry-run`
Expected: one JSON line per business in `local-dev-config.json`, each listing `enabled` platforms. No writes.

- [ ] **Step 4: `onboard-client.py` dry-run end to end**

Run: `python scripts/onboard-client.py add --key demo-co --name "Demo Co" --from-file local-dev-config.json --yes --dry-run --skip-validate`
Expected: prints manifest + `dryRun: true`, no secret writes, prints the next-steps checklist.

- [ ] **Step 5: Final commit (if any lockfiles / incidental changes)**

```bash
git add -A
git commit -m "chore: partial client onboarding — final regression pass" || echo "nothing to commit"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
| --- | --- |
| 1 Client manifest (shape, field rules, "usable") | 2, 3, 4 |
| 2 Storage + precedence + `_index/clients` | 3, 4 |
| 3 `shared/manifest.py` functions | 3, 4 |
| 4 Models | 2 |
| 5 Partial-aware config loading + new error codes | 5 |
| 6 Path A `onboard-client.py` + `push-secrets.py` refactor | 6, 7, 8 |
| 7 Path B signed `/admin/clients*` + auth prefix | 10, 11 |
| 8 Discovery MCP tools | 12 |
| 9 Rules unchanged | 5 (comment), 14 (template in docs) |
| 10 Content agent template + warning | 13 |
| 11 Migration (committed manifests + backfill) | 9 |
| 12 Testing (pytest introduced) | 1 + every task |
| 13 Docs (5 files) | 14 |
| Interfaces summary / rollout order | task order matches spec §rollout |
| Open question: enabled set for existing clients | 9 Step 4 (flagged inline) |
| Open question: region standardization | Global Constraints (resolved: `AWS_REGION`, default `us-east-1`) |

No gaps.

**2. Placeholder scan** — no "TBD"/"handle edge cases"/"similar to Task N". Each code step has real code. `docs/ONBOARDING.md` / `CLIENT_MANIFEST.md` content is enumerated point-by-point rather than pasted verbatim — acceptable for prose docs; the rules template and troubleshooting entries that carry behavioral contract are given verbatim.

**3. Type consistency**

- `PlatformInput(platform, enabled, metadata, credentials)` — consistent across Tasks 6, 7, 8, 11.
- `build_manifest(business_key, display_name, *, tenant_id, industry, status, platform_inputs, existing)` — consistent Tasks 6, 8, 9, 11.
- `apply_onboarding(manifest, platform_inputs, *, dry_run) -> {"manifest","secretsWritten","dryRun"}` — consistent Tasks 6, 8, 11.
- `upsert_client_manifest(manifest, *, dry_run) -> ClientManifest` — Tasks 4, 6, 9.
- `set_platform_config(business_key, platform, *, enabled, metadata, dry_run) -> ClientManifest` — Tasks 4, 7, 11.
- `client_readiness(business_key) -> ReadinessReport | None` — Tasks 4, 7, 11, 12.
- `merge_platform_config(manifest, platform, secret_config) -> dict | None` — Tasks 4, 5.
- `manifest_secret_id` / `credential_secret_id` / `INDEX_SECRET_ID` — one definition (Task 3), used everywhere.
- `KNOWN_PLATFORMS` — defined in `shared/models.py` (Task 2), re-exported by `shared/manifest.py` (Task 3).

Consistent.

**4. Ambiguity check**

- "missing required key" = key absent **or** falsy (`not merged.get(k)`) — stated in Task 4 / Task 5.
- Dry-run always means zero writes (manifest, credential secrets, index) — asserted in Tasks 4, 6, 8, 11.
- `from shared.secrets import X` is forbidden in production code (breaks monkeypatch) — stated in Task 1 Step 3.
- Backfill never writes credential secrets, only manifests — stated in Task 9 Step 3.

Resolved.
