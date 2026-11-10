# Instagram Scheduled Reels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Schedule Instagram Reels through the meta-ads MCP server: approved posts are stored in S3 and an EC2 timer publishes them every 30 minutes.

**Architecture:** A shared S3-backed queue (`shared/media_queue.py`) holds each video and one JSON file per scheduled post. The MCP tool's execute step only uploads to S3 and enqueues — it never calls Instagram. A oneshot runner (`scripts/run-scheduled-social.py`), fired by a systemd timer every 30 minutes, claims due posts and publishes them via the Instagram Content Publishing API using a short-lived presigned S3 URL. TikTok (separate plan) reuses the queue and runner.

**Tech Stack:** Python 3.12, boto3 (S3), FastMCP, requests, systemd timer, Meta Graph API `v23.0`.

**Spec:** the "Design Decisions" section below (agreed in conversation 2026-11-10: S3 only, 30-minute timer).

## Design Decisions

- **Bucket:** existing private bucket `amz-s3-videos-ads-mcp-009959434008-us-east-1-an` in `us-east-1` (created by the user; SSE-S3 and BucketOwnerEnforced already on); name read from env `ADS_MCP_MEDIA_BUCKET`. Setup turns on all four Block Public Access settings and adds lifecycle: `videos/` expire after 90 days, `queue/done/` and `queue/failed/` after 90 days.
- **Layout:** video `videos/<businessKey>/<sha256>.<ext>` (same file uploaded twice is stored once). Queue entry `queue/<state>/<scheduledAtUTC:%Y%m%dT%H%MZ>-<id>.json`, `state` ∈ `pending`, `processing`, `done`, `failed`, `cancelled`. State change = copy to new prefix, then delete old.
- **Entry schema (JSON):** `id` (uuid4 hex), `platform` (`"instagram"`), `businessKey`, `scheduledAt` (UTC ISO `...Z`), `videoKey`, `videoSha256`, `caption`, `options` (`{"shareToFeed": true}`), `createdAt`, `attempts` (int), `lastError` (str|null), `result` (dict|null).
- **Tools (meta-ads server):** `meta_ads_schedule_instagram_reel` (write), `meta_ads_list_scheduled_social` (read), `meta_ads_cancel_scheduled_social` (write). Write tools use `_write_flow` dry run → approvalId; local files bind the approval to SHA-256 (`bound_args`, as in `tools/publish.py`).
- **Video source:** local `file_path` only (`.mp4`/`.mov`, ≤ 1 GiB) — reuse `publish._video_source`; `file_url` is rejected for Instagram (the runner needs the bytes in S3).
- **Caption:** required, ≤ 2,200 characters, ≤ 30 hashtags (`#` tokens), else `REQUEST_INVALID`.
- **Schedule time:** reuse `publish._parse_schedule_time` (naive = Pacific) and the 10-minute / 75-day window; dry run warns "publishes up to 30 minutes after the scheduled time".
- **Instagram account:** `instagram_user_id` from config if set, else `GET /{page_id}?fields=instagram_business_account` (blocking rule check `instagram-linked` when missing). Token needs `instagram_basic`, `instagram_content_publish`.
- **Publish (runner):** `POST /{ig_user_id}/media` `media_type=REELS`, `video_url=<presigned GET, 1 h>`, `caption`, `share_to_feed=true` → container id; poll `GET /{container_id}?fields=status_code` every 10 s up to 5 min until `FINISHED` (`ERROR`/`EXPIRED` ⇒ fail); `POST /{ig_user_id}/media_publish` `creation_id` → media id.
- **Runner rules:** due = `scheduledAt <= now`. Claim by moving `pending → processing` before any Instagram call (no double posts). Success → `done` with `result`. Transient Graph error (`retryable`) → back to `pending`, `attempts += 1`; after 3 attempts or a non-retryable error → `failed`. More than 24 h late → `failed` with `lastError="missed: more than 24h late"`. `processing` older than 1 h → `failed` with `lastError="stale processing; check Instagram before retrying"` (never auto-reposted).
- **Timer:** `ads-mcp-social.timer`, `OnCalendar=*:00/30`, `Persistent=true`; `ads-mcp-social.service` oneshot, `Environment=AWS_REGION=us-east-1`, `ExecStart=/usr/bin/flock -n /tmp/ads-mcp-social.lock /opt/ads-mcp/.venv/bin/python /opt/ads-mcp/scripts/run-scheduled-social.py`.
- **IAM:** inline policy `ads-mcp-media-s3` on role `rd-mcp-ec2-role`: `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on `arn:aws:s3:::<bucket>/*`, `s3:ListBucket` on the bucket. The laptop uses its existing AWS credentials.
- **Cancel:** only `pending` entries; moves to `cancelled`.

## Global Constraints

- Every write tool: dry run without `approval_id`, execute with it (15-minute TTL), per `CLAUDE.md`.
- No credentials in code or committed files; presigned URLs are never logged in full (log the key only).
- `ADS_MCP_MEDIA_BUCKET` unset ⇒ tools and runner raise `AdsMcpError(INTERNAL_ERROR, "ADS_MCP_MEDIA_BUCKET is not configured")`.
- EC2 root disk is 95% full: nothing is written to EC2 disk except logs; the runner streams nothing to disk (Instagram fetches from S3).
- Tests use an in-memory fake S3 (dict of key → bytes) injected via `media_queue._s3_client`; no real AWS calls in tests.

## Prerequisites (user)

1. Switch the Mariachi El Cuis Instagram to a Business/Creator account and connect it to Page 1256871450849413.
2. Regenerate the System User token adding `instagram_basic` and `instagram_content_publish`; put it in `.env` (`META_TOKEN`); Claude updates the secret.

## Review Focus

1. **Runner crashes after Instagram publish but before `done`** → entry is left in `processing`, becomes `failed` after 1 h, never reposted. (Task 3: `test_stale_processing_is_failed_not_retried`)
2. **Two runner processes at once** (manual run during the timer) → `flock -n` in the unit plus claim-by-move: a second claim of the same key fails, publish happens once. (Task 3: `test_claim_is_exclusive`)
3. **EC2 down for a day** → posts more than 24 h late are failed as missed, not dumped all at once. (Task 3: `test_missed_posts_older_than_24h_fail`)
4. **Instagram not linked / token lacks Instagram scopes** → dry run blocked with `instagram-linked` rule check; runner marks the entry failed with the Graph reason. (Task 2: `test_schedule_blocked_when_instagram_not_linked`)
5. **Container processing fails (`ERROR`)** → entry failed with Instagram's status, no `media_publish` call. (Task 3: `test_container_error_fails_without_publish`)

---

### Task 1: S3 media queue

**Files:**
- Create: `shared/media_queue.py`
- Create: `infrastructure/configure-media-bucket.sh`
- Test: `tests/shared/test_media_queue.py`

**Interfaces:**
- Produces (all take `bucket: str | None = None`, defaulting to `ADS_MCP_MEDIA_BUCKET`):
  - `put_video(*, business_key: str, file_path: str, sha256: str) -> str` (returns key; skips upload if the key exists)
  - `enqueue(entry: dict) -> str` (fills `id`, `createdAt`, `attempts=0`, `lastError=None`, `result=None`; returns key under `queue/pending/`)
  - `list_entries(*, states: tuple[str, ...], business_key: str | None = None, platform: str | None = None) -> list[dict]` (each dict gets `"_key"` and `"_state"`; sorted by `scheduledAt`)
  - `move(key: str, to_state: str, *, updates: dict | None = None) -> str` (raises `KeyError` if the source no longer exists — this is the claim)
  - `presigned_get(key: str, *, expires: int = 3600) -> str`
  - `_s3_client()` (module-level factory; tests monkeypatch it)

- [ ] **Step 1: Write the failing tests** (fake S3 implementing `put_object`, `get_object`, `head_object`, `delete_object`, `copy_object`, `list_objects_v2`, `generate_presigned_url`)

```python
def test_enqueue_writes_pending_with_sortable_key(fake_s3):
    key = media_queue.enqueue({"platform": "instagram", "businessKey": "el-cuis", "scheduledAt": "2026-11-12T20:00:00Z", ...})
    assert key.startswith("queue/pending/20261112T2000Z-")

def test_put_video_dedupes_by_sha(fake_s3, tmp_path):  # second call makes no put_object
def test_list_entries_filters_and_sorts(fake_s3):       # business_key + platform filters, ascending scheduledAt
def test_move_applies_updates_and_deletes_source(fake_s3):
def test_move_missing_source_raises_keyerror(fake_s3):
def test_missing_bucket_env_raises(monkeypatch):        # ADS_MCP_MEDIA_BUCKET unset → AdsMcpError INTERNAL_ERROR
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/shared/test_media_queue.py -q` → FAIL (module not found)
- [ ] **Step 3: Implement** `shared/media_queue.py`; `move` = `get_object` (KeyError on `NoSuchKey`) → `put_object` new key → `delete_object` old. `configure-media-bucket.sh` (bucket already exists; fail if it does not): `put-public-access-block` (all true), `put-bucket-encryption` (AES256, idempotent), `put-bucket-lifecycle-configuration` (rules above), and `aws iam put-role-policy --role-name rd-mcp-ec2-role --policy-name ads-mcp-media-s3`.
- [ ] **Step 4: Run tests** — `python -m pytest tests -q` → all PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(shared): S3-backed media queue for scheduled social posts"`

---

### Task 2: Instagram tools (schedule, list, cancel)

**Files:**
- Create: `servers/meta-ads/tools/instagram.py`
- Modify: `shared/meta_ads_client.py` (add `get_instagram_user_id`)
- Modify: `servers/meta-ads/mcp_server.py`, `servers/meta-ads/main.py`
- Test: `tests/meta_ads/test_instagram.py`

**Interfaces:**
- Consumes: Task 1 queue functions; `publish._video_source`, `publish._parse_schedule_time`, `publish._schedule_window_check`, `write._write_flow(bound_args=)`.
- Produces:
  - `meta_ads_client.get_instagram_user_id(config, *, page_id: str, tool=None) -> str | None` (`config["instagram_user_id"]` wins; else the Page's `instagram_business_account.id`; `None` when absent)
  - `instagram.schedule_instagram_reel(request, request_id) -> dict` — payload `filePath`, `scheduledTime`, `caption`; rule checks `instagram-linked` (blocking if `None`), `schedule-window`; dry-run summary includes "publishes up to 30 minutes after"; execute → `put_video` + `enqueue`, returns `{"entryId", "queueKey", "videoKey"}`.
  - `instagram.list_scheduled_social(request, request_id) -> dict` — read; payload optional `states` (default `["pending", "processing", "failed"]`); rows include `scheduledPacific`.
  - `instagram.cancel_scheduled_social(request, request_id) -> dict` — payload `entryId`; blocking `entry-is-pending` unless the entry is in `pending`; execute → `move(key, "cancelled")`.
  - MCP: `meta_ads_schedule_instagram_reel(business_key, file_path, scheduled_time, caption, approval_id=None)`, `meta_ads_list_scheduled_social(business_key, states=None)`, `meta_ads_cancel_scheduled_social(business_key, entry_id, approval_id=None)`; HTTP routes `/tools/schedule_instagram_reel`, `/tools/list_scheduled_social`, `/tools/cancel_scheduled_social`.

- [ ] **Step 1: Write the failing tests**

```python
def test_schedule_dry_run_makes_no_s3_writes(ig_env, fixed_now, tmp_path)       # approvalId; fake_s3 empty
def test_schedule_execute_uploads_and_enqueues(ig_env, fixed_now, tmp_path)     # one videos/ key, one queue/pending/ key; entry.platform == "instagram"
def test_schedule_blocked_when_instagram_not_linked(ig_env, fixed_now, tmp_path)  # get_instagram_user_id → None ⇒ no approvalId
@pytest.mark.parametrize("caption", ["x" * 2201, " ".join(f"#t{i}" for i in range(31)), ""])
def test_schedule_rejects_bad_captions(ig_env, fixed_now, tmp_path, caption)    # REQUEST_INVALID
def test_schedule_rejects_file_url(ig_env, fixed_now)
def test_cancel_only_pending(ig_env)                                            # processing entry ⇒ blocked
def test_list_defaults_hide_done_and_cancelled(ig_env)
def test_routes_and_tools_registered()
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/meta_ads/test_instagram.py -q` → FAIL
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run tests** — `python -m pytest tests -q` → all PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(meta-ads): schedule, list and cancel Instagram Reels via S3 queue"`

---

### Task 3: Runner + Instagram publisher

**Files:**
- Create: `scripts/run-scheduled-social.py`
- Create: `shared/instagram_publish.py`
- Test: `tests/scripts/test_run_scheduled_social.py`, `tests/shared/test_instagram_publish.py`

**Interfaces:**
- Consumes: Task 1 queue; `load_meta_ads_config`, `_graph_request`, `get_instagram_user_id`.
- Produces:
  - `instagram_publish.publish_reel(config, *, ig_user_id: str, video_url: str, caption: str, share_to_feed: bool, sleep=time.sleep, poll_seconds: int = 10, timeout_seconds: int = 300) -> dict` → `{"containerId", "mediaId"}`; raises `AdsMcpError` with `details["containerStatus"]` on `ERROR`/`EXPIRED`/timeout.
  - Runner `run_once(*, now: datetime, publishers: dict[str, Callable[[dict], dict]]) -> dict` → counts `{"published", "retried", "failed", "skipped"}`; `main()` calls it with `{"instagram": publish_instagram_entry}`; exit code 1 if any failed this run.

- [ ] **Step 1: Write the failing tests**

```python
def test_publish_reel_polls_until_finished_then_publishes(fake_graph)        # IN_PROGRESS, FINISHED → media_publish called with creation_id
def test_container_error_fails_without_publish(fake_graph)                   # ERROR → AdsMcpError, no media_publish call
def test_run_once_publishes_due_and_skips_future(fake_s3)                    # due → done with result; future stays pending
def test_transient_error_requeues_then_fails_after_3(fake_s3)                # attempts 1,2 → pending; 3 → failed
def test_missed_posts_older_than_24h_fail(fake_s3)
def test_stale_processing_is_failed_not_retried(fake_s3)                     # processing createdAt-claimed > 1h ago → failed, publisher not called
def test_claim_is_exclusive(fake_s3)                                         # move() of an already-claimed key raises KeyError → runner skips it
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/scripts/test_run_scheduled_social.py tests/shared/test_instagram_publish.py -q` → FAIL
- [ ] **Step 3: Implement.** Record claim time as `updates={"claimedAt": now}` on the `pending → processing` move; stale check uses `claimedAt`. Logging format and `sys.path` setup copied from `scripts/run-scheduled-posts.py`; never log presigned URLs.
- [ ] **Step 4: Run tests** — `python -m pytest tests -q` → all PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(social): scheduled-post runner with Instagram Reels publisher"`

---

### Task 4: Timer, deploy wiring, docs, live check

**Files:**
- Create: `infrastructure/systemd/ads-mcp-social.service`, `infrastructure/systemd/ads-mcp-social.timer`
- Modify: `scripts/deploy.sh` (install + enable the timer; set `ADS_MCP_MEDIA_BUCKET` for meta-ads and the runner), `servers/meta-ads/local_stdio.py` (pass through `ADS_MCP_MEDIA_BUCKET` from `.env`), `README.md`
- Test: `tests/scripts/test_systemd_units.py`

- [ ] **Step 1: Write the failing test** — parse the two unit files: timer has `OnCalendar=*:00/30` and `Persistent=true`; service is `Type=oneshot`, has `AWS_REGION=us-east-1`, and `ExecStart` starts with `/usr/bin/flock -n`.
- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/scripts/test_systemd_units.py -q` → FAIL
- [ ] **Step 3: Implement** the units, deploy wiring (copy `*.timer` with the services, `systemctl enable --now ads-mcp-social.timer`), README section (tools, 30-minute lateness, cancel, failed entries, prerequisites).
- [ ] **Step 4: Run tests** — `python -m pytest tests -q` → all PASS
- [ ] **Step 5: Infra (asks the user first — creates AWS resources):** run `bash infrastructure/configure-media-bucket.sh`; expect Block Public Access all true, lifecycle rules present, and the role policy attached.
- [ ] **Step 6: Live check (after Prerequisites):** dry run `meta_ads_schedule_instagram_reel` 1 hour out → approvalId; execute only with user approval; next timer run publishes; `meta_ads_list_scheduled_social` shows it `done`.
- [ ] **Step 7: Commit, then deploy after merge** — `git commit -m "feat(social): 30-minute systemd timer and docs for Instagram scheduling"`; deploy → 7 health endpoints 200 and `systemctl list-timers | grep ads-mcp-social`.
