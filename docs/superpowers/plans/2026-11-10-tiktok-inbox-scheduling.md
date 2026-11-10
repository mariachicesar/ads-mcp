# TikTok Inbox Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Schedule TikTok videos so that, at the scheduled time, the video lands in the Mariachi El Cuis TikTok app inbox as a draft for the user to tap Post.

**Architecture:** A new `servers/tiktok/` MCP server (stdio first) schedules into the S3 queue built by the Instagram plan, with `platform: "tiktok"`. The existing 30-minute runner gains a TikTok publisher that downloads the video from S3 into memory and sends it with TikTok's Content Posting API inbox flow (`FILE_UPLOAD`). Only the runner talks to TikTok, so only it refreshes OAuth tokens and writes the rotated refresh token back to Secrets Manager.

**Tech Stack:** Python 3.12, FastMCP, requests, boto3 (S3 + Secrets Manager), TikTok Content Posting API v2.

**Spec:** the "Design Decisions" section below (agreed in conversation 2026-11-10: inbox mode, S3 queue, 30-minute timer). **Depends on:** `docs/superpowers/plans/2026-11-10-instagram-scheduled-reels.md` Tasks 1, 3, 4 being merged (queue, runner, timer).

## Design Decisions

- **Mode:** inbox only (`video.upload` scope). TikTok inbox drafts carry **no caption**; the caption is stored on the entry and shown by the list tool so the user can paste it when posting. Direct Post is out of scope until TikTok audits the app.
- **TikTok app (user):** developers.tiktok.com app with products Login Kit + Content Posting API; scopes `user.info.basic`, `video.upload`; redirect URI `https://mariachielcuis.com/` (the user copies `code` from the address bar).
- **Secret:** `/ads-mcp/el-cuis/tiktok/config` = `{"client_key", "client_secret", "refresh_token", "open_id", "refresh_expires_at"}`.
- **Auth script:** `scripts/tiktok-auth.py` prints the authorize URL (`https://www.tiktok.com/v2/auth/authorize/?client_key=…&scope=user.info.basic,video.upload&response_type=code&redirect_uri=…&state=<random>`), reads the pasted `code`, exchanges it at `POST https://open.tiktokapis.com/v2/oauth/token/` (`grant_type=authorization_code`), and writes the secret.
- **Token refresh (runner only):** `POST /v2/oauth/token/` `grant_type=refresh_token` before each TikTok publish; if the response's `refresh_token` differs, write it back with `put_secret_value` **before** using the access token. Access token is never stored.
- **Inbox upload:** `POST https://open.tiktokapis.com/v2/post/publish/inbox/video/init/` with `{"source_info": {"source": "FILE_UPLOAD", "video_size": N, "chunk_size": C, "total_chunk_count": K}}` → `publish_id`, `upload_url`. Chunking: if `N <= 64 MiB` one chunk (`C = N`); else `C = 10 MiB`, `K = N // C` (the last chunk absorbs the remainder, ≤ 2C). Each chunk `PUT upload_url` with `Content-Type: video/mp4`, `Content-Range: bytes start-end/N`. Then poll `POST /v2/post/publish/status/fetch/` `{"publish_id"}` every 10 s up to 5 min until `SEND_TO_USER_INBOX` (`FAILED` ⇒ fail with `fail_reason`).
- **Memory, not disk:** EC2 disk is 95% full — the runner streams the S3 object in chunks with ranged `get_object`, never writing to disk.
- **Limits:** video ≤ 1 GiB local file (reuse `publish._video_source`), `.mp4`/`.mov`. Rule check `tiktok-daily-limit`: blocking if 5 or more tiktok entries for this business are `pending` with `scheduledAt` within 24 h of the new one (TikTok caps pending inbox shares per user per day).
- **Tools (tiktok server):** `tiktok_schedule_inbox_video` (write), `tiktok_list_scheduled` (read), `tiktok_cancel_scheduled` (write). Same dry-run → approvalId flow; `SERVICE_NAME = "tiktok"`.
- **Server shape:** `servers/tiktok/{mcp_server.py,local_stdio.py,tools/schedule.py,requirements.txt}`; no FastAPI service yet (YAGNI: only needed for remote clients).

## Global Constraints

- Every write tool: dry run without `approval_id`, execute with it (15-minute TTL), per `CLAUDE.md`.
- No credentials in code or committed files; never log access/refresh tokens or upload URLs.
- The MCP tools never call TikTok (only S3); all TikTok traffic is in the runner.
- Tests: fake S3 from the Instagram plan; fake `requests` for TikTok; fake Secrets Manager client. No network.

## Prerequisites (user)

1. Create the TikTok developer app as above; give Claude `client_key` and `client_secret` (they go straight into Secrets Manager).
2. Run `python scripts/tiktok-auth.py` once and paste the `code` when asked.

## Review Focus

1. **Refresh token rotates and the write-back fails** → runner must not use the new access token (the old refresh token may be dead); entry back to `pending`, error logged. (Task 2: `test_refresh_writeback_failure_aborts_publish`)
2. **Video size not a multiple of the chunk size** → last chunk carries the remainder and `Content-Range` ends at `N-1`. (Task 2: `test_chunk_plan_last_chunk_absorbs_remainder`)
3. **User never opens the inbox** → status stays `SEND_TO_USER_INBOX`; the entry is `done` with a `result.note` saying it awaits the user; list tool shows the caption to paste. (Task 2: `test_inbox_status_marks_done_with_note`)
4. **Sixth TikTok in 24 h** → dry run blocked by `tiktok-daily-limit`, no approvalId. (Task 1: `test_daily_limit_blocks_sixth`)
5. **Authorize redirect returns a different `state`** → `tiktok-auth.py` refuses to exchange the code. (Task 3: `test_auth_rejects_state_mismatch`)

---

### Task 1: TikTok MCP server + schedule/list/cancel tools

**Files:**
- Create: `servers/tiktok/mcp_server.py`, `servers/tiktok/local_stdio.py`, `servers/tiktok/tools/__init__.py`, `servers/tiktok/tools/schedule.py`, `servers/tiktok/requirements.txt`
- Test: `tests/tiktok/test_schedule.py`

**Interfaces:**
- Consumes: `shared.media_queue` (`put_video`, `enqueue`, `list_entries`, `move`); `servers/meta-ads/tools/publish.py` helpers are **copied into `shared/scheduling.py`** first (Task 1 Step 3) so both servers import them from `shared`: `video_source`, `parse_schedule_time`, `schedule_window_check`, `sha256_file`. meta-ads `publish.py` re-imports them from `shared.scheduling` (names keep their leading-underscore aliases there so existing tests pass).
- Produces: `schedule.schedule_inbox_video(request, request_id)`, `schedule.list_scheduled(request, request_id)`, `schedule.cancel_scheduled(request, request_id)`; MCP `tiktok_schedule_inbox_video(business_key, file_path, scheduled_time, caption=None, approval_id=None)`, `tiktok_list_scheduled(business_key, states=None)`, `tiktok_cancel_scheduled(business_key, entry_id, approval_id=None)`. Entry `platform="tiktok"`, `options={}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_dry_run_no_writes_and_notes_no_caption(tt_env, fixed_now, tmp_path)   # summary mentions inbox + caption not attached
def test_execute_enqueues_tiktok_entry(tt_env, fixed_now, tmp_path)
def test_daily_limit_blocks_sixth(tt_env, fixed_now, tmp_path)                  # 5 pending within 24h → blocked
def test_cancel_only_pending(tt_env)
def test_list_shows_caption_and_pacific_time(tt_env)
def test_meta_publish_helpers_still_importable()                                 # publish._video_source etc. still exist
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/tiktok -q` → FAIL
- [ ] **Step 3: Implement** `shared/scheduling.py` (move helpers, aliases in `publish.py`), then the tiktok server mirroring `servers/meta-ads` structure; `local_stdio.py` reads `ADS_MCP_MEDIA_BUCKET` from `.env`.
- [ ] **Step 4: Run tests** — `python -m pytest tests -q` → all PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(tiktok): MCP server to schedule TikTok inbox videos via S3 queue"`

---

### Task 2: TikTok client + runner publisher

**Files:**
- Create: `shared/tiktok_client.py`
- Modify: `scripts/run-scheduled-social.py` (register `"tiktok": publish_tiktok_entry`)
- Test: `tests/shared/test_tiktok_client.py`, `tests/scripts/test_run_scheduled_social.py`

**Interfaces:**
- Produces:
  - `tiktok_client.refresh_access_token(config: dict, *, secret_id: str, secrets_client=None) -> str`
  - `tiktok_client.chunk_plan(size: int) -> list[tuple[int, int]]` (inclusive byte ranges)
  - `tiktok_client.send_to_inbox(access_token: str, *, size: int, read_range: Callable[[int, int], bytes], sleep=time.sleep) -> dict` → `{"publishId", "status"}`
  - Runner `publish_tiktok_entry(entry: dict) -> dict` (reads S3 with ranged `get_object`).

- [ ] **Step 1: Write the failing tests**

```python
def test_chunk_plan_single_chunk_under_64mib()
def test_chunk_plan_last_chunk_absorbs_remainder()        # size = 100 MiB + 3 → 10 ranges, first (0, 10 MiB - 1), last (90 MiB, 100 MiB + 2), contiguous
def test_refresh_writes_back_rotated_token(fake_tt, fake_secrets)
def test_refresh_writeback_failure_aborts_publish(fake_tt, fake_secrets)   # put_secret_value raises → publisher raises retryable error, no init call
def test_send_to_inbox_puts_chunks_with_content_range(fake_tt)
def test_inbox_status_marks_done_with_note(fake_s3, fake_tt)               # runner: entry done, result.note mentions inbox
def test_tiktok_failed_status_fails_entry(fake_s3, fake_tt)
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/shared/test_tiktok_client.py tests/scripts/test_run_scheduled_social.py -q` → FAIL
- [ ] **Step 3: Implement** per Design Decisions (endpoints, headers, 10 s / 5 min poll).
- [ ] **Step 4: Run tests** — `python -m pytest tests -q` → all PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(social): TikTok inbox publisher with rotating refresh tokens"`

---

### Task 3: One-time auth script, registration, docs, live check

**Files:**
- Create: `scripts/tiktok-auth.py`
- Modify: `README.md`; register the tiktok stdio server (`claude mcp add tiktok -- .venv/Scripts/python.exe servers/tiktok/local_stdio.py`, documented, run by the user)
- Test: `tests/scripts/test_tiktok_auth.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_auth_url_has_scopes_redirect_and_state()
def test_auth_rejects_state_mismatch()
def test_auth_exchange_writes_secret(fake_tt, fake_secrets)   # secret has refresh_token, open_id, refresh_expires_at
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/scripts/test_tiktok_auth.py -q` → FAIL
- [ ] **Step 3: Implement** the script (accepts the full pasted redirect URL or the bare `code`; validates `state` when a URL is pasted); README section (app setup, inbox behavior, no captions on drafts, daily limit, 30-minute lateness).
- [ ] **Step 4: Run tests** — `python -m pytest tests -q` → all PASS
- [ ] **Step 5: Live check (after Prerequisites, user approval for execute):** dry run `tiktok_schedule_inbox_video` 40 minutes out → approvalId; execute on approval; after the next timer run the draft appears in the TikTok app inbox; `tiktok_list_scheduled` shows `done`.
- [ ] **Step 6: Commit, then deploy after merge** — `git commit -m "feat(tiktok): auth script and docs for TikTok inbox scheduling"`
