# Meta Video Publishing (Facebook + Ad Library) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the meta-ads MCP server upload videos to the ad Media library and schedule Facebook Page videos and Reels at a future time, all through the dry-run → approvalId flow.

**Architecture:** A new `servers/meta-ads/tools/publish.py` module holds the four tools; it reuses `_write_flow` from `tools/write.py` (extended so an approval can be bound to a file's SHA-256, not just the arguments). Facebook's own scheduler holds scheduled posts (`published=false` + `scheduled_publish_time`), so no queue, timer or storage is added. `shared/meta_ads_client.py` gains multipart upload, a per-call access-token override (Page token), and the Reels resumable-upload calls.

**Tech Stack:** Python 3.12, FastMCP, FastAPI, `requests`, pytest, Meta Graph API `v23.0`.

**Spec:** the "Design Decisions" section below (agreed in conversation 2026-11-10). Instagram is out of scope; it gets its own plan once a queue/storage decision is made.

## Design Decisions

- **Tools:** `meta_ads_upload_ad_video` (write), `meta_ads_schedule_page_video` (write), `meta_ads_schedule_page_reel` (write), `meta_ads_list_scheduled_page_videos` (read).
- **Video source:** exactly one of `file_path` (local file readable by the server process) or `file_url` (public `https://` URL Meta fetches).
- **Local files:** extensions `.mp4` or `.mov`, max `1_073_741_824` bytes (1 GiB, Meta's non-resumable limit). Larger files are rejected with `REQUEST_INVALID`, not chunked.
- **Approval binding:** for `file_path`, the dry run hashes the file and the approval is bound to `{**payload, "fileSha256": <hex>}`. Executing after the file changed fails approval verification.
- **Schedule time:** ISO 8601. Naive values are `America/Los_Angeles` (same rule as `scripts/run-scheduled-posts.py`). Must be ≥ 10 minutes and ≤ 75 days from now, else a **blocking** rule check (no approvalId). Sent to Meta as a Unix timestamp.
- **Page guard:** `page_id` defaults to the configured `page_id`; any other value is a **blocking** rule check (`page-is-configured`).
- **Page token:** fetched at call time via `GET /{page_id}?fields=access_token` with the configured user token; never stored.
- **Endpoints:**
  - Ad video: `POST https://graph-video.facebook.com/v23.0/{act_id}/advideos` (`source` multipart or `file_url`, `name`).
  - Page video: `POST https://graph-video.facebook.com/v23.0/{page_id}/videos` (`source`|`file_url`, `title`, `description`, `published=false`, `scheduled_publish_time`), Page token.
  - Reel: `POST /{page_id}/video_reels` `upload_phase=start` → upload bytes to `https://rupload.facebook.com/video-upload/v23.0/{video_id}` (headers `Authorization: OAuth <page token>`, `offset: 0`, `file_size: <bytes>`; or header `file_url: <url>`) → `POST /{page_id}/video_reels` `upload_phase=finish`, `video_id`, `video_state=SCHEDULED`, `scheduled_publish_time`, `description`. Page token throughout.
  - List: `GET /{page_id}/videos?fields=id,title,description,scheduled_publish_time,published,created_time` with the Page token, keep rows where `published` is false and `scheduled_publish_time` is set.
- **Cancel/edit** a scheduled post: out of scope (do it in Meta Business Suite).

## Global Constraints

- Every write tool supports dry-run (no `approval_id`) and execute (`approval_id` from the dry run, same args, 15-minute TTL), per `CLAUDE.md`.
- No credentials in code or committed files; tokens never appear in URLs except `appsecret_proof`.
- `appsecret_proof` must be the HMAC-SHA256 of **the token actually sent** (Page token when overridden).
- Graph API version comes from `config["api_version"]`, default `DEFAULT_API_VERSION` (`v23.0`); do not hard-code it in tools.
- Business key for tests: `el-cuis`; test config `{"ad_account_id": "act_123", "access_token": "tok", "app_secret": "sec", "page_id": "999"}` (same as `tests/meta_ads/test_write.py`).

## Prerequisites (user, before Task 5's live check)

- Regenerate the Meta user token with `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, `ads_management`, `ads_read`, `business_management`; update `META_TOKEN` in `.env` and `access_token` in the `/ads-mcp/el-cuis/meta-ads/config` secret.

## Review Focus

1. **File edited between dry run and approval** → execute must fail approval verification, not upload the new file. (Task 2: `test_upload_ad_video_rejects_changed_file`)
2. **Naive time across the DST change** → `2026-11-01T09:00:00` must mean 09:00 PST (17:00 UTC), not PDT. (Task 3: `test_parse_schedule_time_naive_is_pacific_after_dst`)
3. **Time in the past, < 10 min, or > 75 days** → blocked, no approvalId, nothing sent. (Task 3: `test_schedule_page_video_blocks_out_of_window_times`)
4. **Reel bytes uploaded but `finish` fails** → error carries `videoId` and a note so the orphan can be found. (Task 4: `test_schedule_page_reel_finish_failure_reports_video_id`)
5. **Token missing Page permissions** → Page-token fetch failure surfaces as `UPSTREAM_ERROR` naming `pages_manage_posts`, not a generic crash. (Task 1: `test_get_page_access_token_missing_permission_message`)

---

### Task 1: Graph client — uploads, token override, Page token, Reels upload

**Files:**
- Modify: `shared/meta_ads_client.py` (`_graph_request` at ~line 207; add new functions after `get_adset_schedule`)
- Test: `tests/meta_ads/test_client_publish.py` (new)

**Interfaces:**
- Produces:
  - `GRAPH_VIDEO_URL = "https://graph-video.facebook.com"`; `RUPLOAD_URL = "https://rupload.facebook.com/video-upload"`
  - `_graph_request(config, method, path, *, params=None, data=None, files=None, base_url=None, access_token=None, tool=None) -> dict` — `files` is passed to `requests.request(files=...)`; `base_url` defaults to `GRAPH_BASE_URL`; `access_token` overrides `config["access_token"]` for the header **and** `appsecret_proof`.
  - `get_page_access_token(config, *, page_id: str, tool=None) -> str`
  - `post_video(config, *, path: str, fields: dict, file_path: str | None, file_url: str | None, access_token: str | None = None, tool=None) -> dict` — POST to `GRAPH_VIDEO_URL`; opens `file_path` as `files={"source": fh}` or sets `fields["file_url"]`.
  - `upload_reel_bytes(config, *, video_id: str, access_token: str, file_path: str | None, file_url: str | None, tool=None) -> dict` — POST to `f"{RUPLOAD_URL}/{version}/{video_id}"` with the headers in Design Decisions; errors raise `AdsMcpError(UPSTREAM_ERROR)` with `details["videoId"]`.

- [ ] **Step 1: Write the failing tests** (stub `meta_ads_client.import_module` with a fake `requests`, as `test_graph_request_sends_token_in_header_not_url` does)

```python
def test_token_override_changes_header_and_proof(fake_requests):
    meta_ads_client._graph_request(CONFIG, "GET", "999", access_token="page-tok")
    call = fake_requests.calls[-1]
    assert call["headers"]["Authorization"] == "Bearer page-tok"
    assert call["params"]["appsecret_proof"] == hmac.new(b"sec", b"page-tok", hashlib.sha256).hexdigest()

def test_post_video_uses_graph_video_host_and_multipart(fake_requests, tmp_path):
    f = tmp_path / "v.mp4"; f.write_bytes(b"x")
    meta_ads_client.post_video(CONFIG, path="act_123/advideos", fields={"name": "V"}, file_path=str(f), file_url=None)
    call = fake_requests.calls[-1]
    assert call["url"] == "https://graph-video.facebook.com/v23.0/act_123/advideos"
    assert "source" in call["files"] and call["data"]["name"] == "V"

def test_post_video_with_url_sends_file_url_and_no_files(fake_requests): ...
    # assert call["files"] is None and call["data"]["file_url"] == "https://cdn.example/v.mp4"

def test_upload_reel_bytes_headers(fake_requests, tmp_path):
    # 3-byte file → headers Authorization "OAuth page-tok", offset "0", file_size "3";
    # url "https://rupload.facebook.com/video-upload/v23.0/vid1"

def test_get_page_access_token_missing_permission_message(fake_requests):
    # fake returns {"id": "999"} with no access_token →
    # AdsMcpError(error_code="UPSTREAM_ERROR") whose message contains "pages_manage_posts"
```

- [ ] **Step 2: Run to verify failure**
Run: `python -m pytest tests/meta_ads/test_client_publish.py -q`
Expected: FAIL (`post_video` / `get_page_access_token` not defined; unexpected keyword `access_token`)

- [ ] **Step 3: Implement** the Interfaces above in `shared/meta_ads_client.py`. When `files` is set, send `data=form` and `files=files` (no JSON body). `upload_reel_bytes` sends the raw file body (`data=fh`) and does not go through `_graph_request` (different auth header scheme), but reuses `_graph_error_details` for errors.

- [ ] **Step 4: Run all meta tests**
Run: `python -m pytest tests/meta_ads -q`
Expected: all PASS (existing `_graph_request` callers unaffected)

- [ ] **Step 5: Commit**
```bash
git add shared/meta_ads_client.py tests/meta_ads/test_client_publish.py
git commit -m "feat(meta-ads): graph client support for video uploads and page tokens"
```

---

### Task 2: `upload_ad_video` tool + file-bound approvals

**Files:**
- Modify: `servers/meta-ads/tools/write.py` (`_write_flow`, ~line 127)
- Create: `servers/meta-ads/tools/publish.py`
- Test: `tests/meta_ads/test_publish.py` (new; reuse the `graph`-style fixture pattern from `test_write.py`, monkeypatching names on the `publish` module)

**Interfaces:**
- Consumes: `post_video` (Task 1).
- Produces:
  - `_write_flow(..., bound_args: dict | None = None)` — when given, used instead of `request.payload` for `issue_approval` and `verify_approval`.
  - `publish._video_source(payload: dict, tool: str) -> tuple[dict, str | None]` — returns `({"filePath": ...} | {"fileUrl": ...}, sha256_hex | None)`; raises `REQUEST_INVALID` for: both/neither given, missing file, extension not `.mp4`/`.mov`, size > 1 GiB, URL not `https://`.
  - `publish.upload_ad_video(request: ToolRequest, request_id: str | None) -> dict` — payload `filePath|fileUrl`, `name` (required). Preview `graphRequest` path `"{act_id}/advideos"`, plus `file: {"path"|"url", "bytes", "sha256"}`. Execute returns `{"videoId": ...}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_upload_ad_video_dry_run_binds_sha(publish_env, tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"abc")
    r = publish.upload_ad_video(_req({"filePath": str(f), "name": "V1"}), None)
    assert r["approvalId"] and r["data"]["file"]["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert publish_env["posts"] == []

def test_upload_ad_video_executes_once(publish_env, tmp_path):
    # dry run then execute with approvalId → publish_env["posts"] == [("act_123/advideos", {"name": "V1"})]
    # and result["data"]["result"] == {"videoId": "new-video"}

def test_upload_ad_video_rejects_changed_file(publish_env, tmp_path):
    # dry run, then f.write_bytes(b"abcd"), then execute → raises AdsMcpError; no posts made

@pytest.mark.parametrize("payload", [
    {"name": "V"},                                                    # no source
    {"name": "V", "filePath": "x.mp4", "fileUrl": "https://a/b.mp4"}, # both
    {"name": "V", "fileUrl": "http://a/b.mp4"},                       # not https
    {"name": "V", "filePath": "missing.mp4"},                         # missing file
])
def test_upload_ad_video_rejects_bad_sources(publish_env, payload): ...

def test_video_source_rejects_wrong_extension_and_oversize(tmp_path, monkeypatch):
    # .avi → REQUEST_INVALID; monkeypatch publish.MAX_VIDEO_BYTES = 2 and a 3-byte .mp4 → REQUEST_INVALID
```

- [ ] **Step 2: Run to verify failure**
Run: `python -m pytest tests/meta_ads/test_publish.py -q`
Expected: FAIL (`tools.publish` not found)

- [ ] **Step 3: Implement** `bound_args` in `_write_flow`, then `publish.py` with `MAX_VIDEO_BYTES = 1_073_741_824`, `VIDEO_EXTENSIONS = {".mp4", ".mov"}`, `_video_source`, `upload_ad_video`. Hash in 1 MiB chunks. `bound_args = {**request.payload, "fileSha256": sha}` when `sha` is not None. Rule checks: `_currency_check` is not needed; add an `account-ownership`-style info check that the target is `configured_account_id(config)`.

- [ ] **Step 4: Run tests**
Run: `python -m pytest tests/meta_ads -q`
Expected: all PASS

- [ ] **Step 5: Commit**
```bash
git add servers/meta-ads/tools/write.py servers/meta-ads/tools/publish.py tests/meta_ads/test_publish.py
git commit -m "feat(meta-ads): upload_ad_video tool with file-hash bound approvals"
```

---

### Task 3: `schedule_page_video` (native Facebook scheduling)

**Files:**
- Modify: `servers/meta-ads/tools/publish.py`
- Test: `tests/meta_ads/test_publish.py`

**Interfaces:**
- Consumes: `_video_source`, `bound_args` (Task 2); `get_page_access_token`, `post_video` (Task 1).
- Produces:
  - `publish._now() -> datetime` (aware UTC; monkeypatched in tests).
  - `publish._parse_schedule_time(value: str, tool: str) -> datetime` — aware UTC; naive ⇒ `ZoneInfo("America/Los_Angeles")`; unparseable ⇒ `REQUEST_INVALID`.
  - `publish._schedule_window_check(when: datetime) -> dict` — rule `schedule-window`; blocking unless `_now() + 10 min <= when <= _now() + 75 days`.
  - `publish._page_check(config: dict, page_id: str) -> dict` — rule `page-is-configured`; blocking if `page_id != config["page_id"]`.
  - `publish.schedule_page_video(request, request_id) -> dict` — payload `filePath|fileUrl`, `scheduledTime` (required), `description` (required), `title` (optional), `pageId` (optional). Fields sent: `title`, `description`, `published=False`, `scheduled_publish_time=<int unix>`. Path `"{page_id}/videos"`, Page token. Execute returns `{"videoId": ...}`. The Page token is fetched only in `execute`, never in the dry run.

- [ ] **Step 1: Write the failing tests** (`_now` fixed at `2026-11-10T16:00:00Z`)

```python
def test_parse_schedule_time_naive_is_pacific_after_dst():
    assert publish._parse_schedule_time("2026-11-01T09:00:00", "t") == datetime(2026, 11, 1, 17, 0, tzinfo=timezone.utc)

def test_parse_schedule_time_keeps_explicit_offset():
    assert publish._parse_schedule_time("2026-11-12T12:00:00-08:00", "t").hour == 20

def test_schedule_page_video_dry_run_fields(publish_env, tmp_path):
    # scheduledTime "2026-11-12T12:00:00" → fields["scheduled_publish_time"] == 1794513600,
    # fields["published"] is False, path "999/videos", approvalId present, no page-token fetch

@pytest.mark.parametrize("when", ["2026-11-10T08:05:00", "2026-11-09T12:00:00", "2027-02-01T12:00:00"])
def test_schedule_page_video_blocks_out_of_window_times(publish_env, tmp_path, when):
    # → "approvalId" not in response; schedule-window check failed

def test_schedule_page_video_blocks_other_page(publish_env, tmp_path):
    # pageId "555" → page-is-configured failed, no approvalId

def test_schedule_page_video_execute_uses_page_token(publish_env, tmp_path):
    # execute → post_video called with access_token="page-tok" and path "999/videos"
```

- [ ] **Step 2: Run to verify failure**
Run: `python -m pytest tests/meta_ads/test_publish.py -q -k "schedule or parse"`
Expected: FAIL (`_parse_schedule_time` not defined)

- [ ] **Step 3: Implement** the Interfaces above in `publish.py`. Constants: `SCHEDULE_TZ = ZoneInfo("America/Los_Angeles")`, `MIN_LEAD = timedelta(minutes=10)`, `MAX_LEAD = timedelta(days=75)`. Dry-run summary names the Pacific-time and UTC times.

- [ ] **Step 4: Run tests**
Run: `python -m pytest tests/meta_ads -q`
Expected: all PASS

- [ ] **Step 5: Commit**
```bash
git add servers/meta-ads/tools/publish.py tests/meta_ads/test_publish.py
git commit -m "feat(meta-ads): schedule_page_video with native Facebook scheduling"
```

---

### Task 4: `schedule_page_reel` (three-phase Reels upload)

**Files:**
- Modify: `servers/meta-ads/tools/publish.py`
- Test: `tests/meta_ads/test_publish.py`

**Interfaces:**
- Consumes: Task 3 helpers; `get_page_access_token`, `_graph_request` (with `access_token`), `upload_reel_bytes` (Task 1).
- Produces: `publish.schedule_page_reel(request, request_id) -> dict` — payload `filePath|fileUrl`, `scheduledTime`, `description` (required), `pageId` (optional). Same rule checks as Task 3. Preview lists the 3 steps. Execute: start (`upload_phase=start`) → `upload_reel_bytes` → finish (`upload_phase=finish`, `video_id`, `video_state=SCHEDULED`, `scheduled_publish_time`, `description`); returns `{"videoId": ..., "finish": <finish response>}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_schedule_page_reel_calls_start_upload_finish_in_order(publish_env, tmp_path):
    # execute → publish_env["calls"] == ["start", "upload:vid1", "finish:vid1:SCHEDULED:1794513600"]

def test_schedule_page_reel_finish_failure_reports_video_id(publish_env, tmp_path):
    # finish raises AdsMcpError → re-raised with details["videoId"] == "vid1" and
    # details["note"] == "Reel bytes were uploaded but the reel was not scheduled."

def test_schedule_page_reel_dry_run_makes_no_calls(publish_env, tmp_path):
    # dry run → approvalId present; publish_env["calls"] == []
```

- [ ] **Step 2: Run to verify failure**
Run: `python -m pytest tests/meta_ads/test_publish.py -q -k reel`
Expected: FAIL (`schedule_page_reel` not defined)

- [ ] **Step 3: Implement** `schedule_page_reel` (pattern for the failure note: `create_ad`'s `execute` in `write.py`).

- [ ] **Step 4: Run tests**
Run: `python -m pytest tests/meta_ads -q`
Expected: all PASS

- [ ] **Step 5: Commit**
```bash
git add servers/meta-ads/tools/publish.py tests/meta_ads/test_publish.py
git commit -m "feat(meta-ads): schedule_page_reel via resumable Reels upload"
```

---

### Task 5: List tool, MCP + HTTP registration, docs, live check

**Files:**
- Modify: `servers/meta-ads/tools/publish.py`, `servers/meta-ads/mcp_server.py`, `servers/meta-ads/main.py`, `README.md`
- Test: `tests/meta_ads/test_publish.py`

**Interfaces:**
- Consumes: all `publish.py` tools.
- Produces:
  - `publish.list_scheduled_page_videos(request, request_id) -> dict` — read mode; `data.videos` = rows with `published is False` and `scheduled_publish_time` set, each with `scheduledPacific` (ISO string in `America/Los_Angeles`).
  - MCP tools (snake_case params mapped to camelCase payload, same as `meta_ads_create_ad`): `meta_ads_upload_ad_video(business_key, name, file_path=None, file_url=None, approval_id=None)`, `meta_ads_schedule_page_video(business_key, scheduled_time, description, file_path=None, file_url=None, title=None, page_id=None, approval_id=None)`, `meta_ads_schedule_page_reel(business_key, scheduled_time, description, file_path=None, file_url=None, page_id=None, approval_id=None)`, `meta_ads_list_scheduled_page_videos(business_key, page_id=None)`.
  - HTTP routes `POST /tools/upload_ad_video`, `/tools/schedule_page_video`, `/tools/schedule_page_reel`, `/tools/list_scheduled_page_videos`.

- [ ] **Step 1: Write the failing tests**

```python
def test_list_scheduled_page_videos_filters_and_converts(publish_env):
    # rows: published False + time 1794513600 (kept, scheduledPacific "2026-11-12T12:00:00-08:00"),
    # published True (dropped), published False without time (dropped)

def test_http_routes_registered():
    main = load_service_module("meta-ads", "main")
    paths = {r.path for r in main.app.routes}
    assert {"/tools/upload_ad_video", "/tools/schedule_page_video",
            "/tools/schedule_page_reel", "/tools/list_scheduled_page_videos"} <= paths
```

- [ ] **Step 2: Run to verify failure**
Run: `python -m pytest tests/meta_ads/test_publish.py -q -k "list or routes"`
Expected: FAIL

- [ ] **Step 3: Implement** the list tool and registrations; add a README section "Meta video publishing" covering the four tools, the 10-minute–75-day window, Pacific default, the 1 GiB local-file limit, and the token permissions from Prerequisites.

- [ ] **Step 4: Run full suite**
Run: `python -m pytest tests -q`
Expected: all PASS

- [ ] **Step 5: Live check (after Prerequisites; dry runs only unless the user approves)**
Reconnect meta-ads (`/mcp`), then dry-run `meta_ads_list_scheduled_page_videos` and `meta_ads_schedule_page_reel` with a real file 1 day out. Expected: list returns `ok: true`; the reel dry run shows the 3 steps, Pacific/UTC times and an approvalId. Execute only with explicit user approval.

- [ ] **Step 6: Commit and deploy**
```bash
git add servers/meta-ads README.md tests/meta_ads/test_publish.py
git commit -m "feat(meta-ads): register video publishing tools and document them"
bash scripts/deploy.sh   # expect all 7 health endpoints -> 200
```

---

## Out of Scope / Follow-up Plan

**Instagram Reels scheduling** needs: (1) a durable queue reachable by an EC2 timer, (2) the video stored where EC2 can read it (Instagram's draft post expires after 24 h, so it can't be created at schedule time), (3) `instagram_basic` + `instagram_content_publish`. This conflicts with `docs/WORKFLOW_RUNS.md` ("ads-mcp is stateless for persistence") unless the queue lives in backend-rc or a new S3/DynamoDB store — decide before planning it.
