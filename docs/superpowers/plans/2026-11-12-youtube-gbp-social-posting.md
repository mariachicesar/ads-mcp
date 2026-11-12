# YouTube + GBP Social Posting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every video approved in a `/schedule-social` batch for El Cuis is also scheduled to YouTube (same file, same slot) and gets a Google Business Profile post (frame + Call button, same slot), and every caption carries 626-922-0091.

**Architecture:** YouTube schedules natively: a local stdio tool uploads the video as private with `publishAt`, and a "verify" entry in the S3 media queue lets the EC2 runner confirm after the slot that it went public. GBP has no native scheduling: a local stdio tool extracts a frame with ffmpeg, puts it in S3 and enqueues a `gbp` entry; the EC2 runner creates the local post at the slot. Both follow the existing pattern: `scheduling.write_flow` for dry-run → approvalId → execute, `social_ledger.run_and_mark` for the posting log, `media_queue` for anything published later. A per-platform start snapshot in the profile keeps old songs off the new platforms.

**Tech Stack:** Python 3.12, FastMCP, google-api-python-client (YouTube Data API v3, GBP v4 via existing `servers/gbp/gbp_client.py`), boto3 S3, ffmpeg/ffprobe on the user's PC, pytest with `tests/fake_s3.py`.

**Spec:** `docs/superpowers/specs/2026-11-11-youtube-gbp-social-posting-design.md`

## Global Constraints

- Business key `el-cuis`; phone `626-922-0091`.
- Ledger/profile platform names: `"youtube"` and `"gbp"`.
- YouTube title: non-empty, ≤100 chars, starts with the song name, no `<` or `>`.
- YouTube upload: `privacyStatus: "private"`, `publishAt` = slot (UTC, RFC 3339), `selfDeclaredMadeForKids: false`, `categoryId: "10"` (Music).
- Shorts = vertical (height > width) and duration ≤ 180 s; otherwise "regular video" (still posts).
- GBP post: `topicType: "STANDARD"`, `callToAction: {"actionType": "CALL"}`, one PHOTO; text ≤1,500 chars including the runner-appended link line; **no phone number in GBP text**.
- Phone rule (blocking) applies to Facebook, Instagram, TikTok captions and the YouTube description; tolerated formats `626-922-0091`, `(626) 922-0091`, `626.922.0091`, `+1 626 922 0091` (compare digits).
- Every write tool: dry run without `approval_id`, execute with it, approvalIds expire after 15 min (CLAUDE.md rules 7–8).
- Unknown-outcome failures must not mark the posting log `failed` (runner `_fail(..., definite=False)`, commit 808d7ac).
- No credentials in code or committed files; configs live in Secrets Manager `/ads-mcp/el-cuis/{youtube,gbp}/config`.
- GBP config keys: `client_id`, `client_secret`, `refresh_token`, `gbp_account_id`, `gbp_location_id`.

## Review Focus

1. **A song already scheduled before switch-on** (in ledger or pending queue) later fails, is cancelled, or comes back as a re-export → it is never offered to youtube/gbp. Test in Task 2.
2. **YouTube upload succeeds but the posting log write fails** → result carries `ledgerWarning`, no second upload is offered while the verify queue entry exists (scan already treats queue entries as queued). Test in Task 3.
3. **YouTube video still private after its slot** (e.g. processing stuck, policy) → runner fails the verify entry with `definite=False`; ledger stays `queued`; never re-offered. Test in Task 4.
4. **GBP runner runs before the YouTube video is public, or there is no YouTube record** → GBP post uses the website link line, never a dead YouTube link. Test in Task 6.
5. **ffmpeg/ffprobe missing or the file unreadable** → GBP dry run is blocked with a clear message; YouTube dry run still works and reports "Shorts: unknown". Tests in Tasks 3 and 5.

---

### Task 1: New platforms and the phone rule

**Files:**
- Modify: `shared/social_ledger.py:22` (PLATFORMS)
- Modify: `shared/caption_rules.py` (`check_caption`)
- Modify: `social/profiles/el-cuis.json` (add `"phone"`; do NOT add the new platforms yet, Task 7 does)
- Test: `tests/shared/test_caption_rules.py`, `tests/shared/test_social_ledger.py`

**Interfaces:**
- Produces: `social_ledger.PLATFORMS == ("facebook", "instagram", "tiktok", "youtube", "gbp")`; `caption_rules.has_phone(text: str, phone: str) -> bool`; `check_caption` emits a rule `"phone"` only when `profile.get("phone")` is set.

- [ ] **Step 1: Write the failing tests**

```python
PROFILE = {**BASE_PROFILE, "phone": "626-922-0091"}

@pytest.mark.parametrize("text", ["Llama 626-922-0091", "(626) 922-0091", "626.922.0091", "+1 626 922 0091"])
def test_phone_rule_accepts_formats(text):
    assert _rule(check_caption(PROFILE, GOOD_CAPTION + "\n" + text), "phone")["passed"]

def test_phone_rule_blocks_missing_phone():
    check = _rule(check_caption(PROFILE, GOOD_CAPTION), "phone")
    assert not check["passed"] and check["severity"] == "blocking" and "626-922-0091" in check["message"]

def test_no_phone_rule_without_profile_phone():
    assert all(c["rule"] != "phone" for c in check_caption(BASE_PROFILE, GOOD_CAPTION))

def test_ledger_accepts_youtube_and_gbp(fake_s3):
    for p in ("youtube", "gbp"):
        assert social_ledger.mark("el-cuis", song_key="reloj", sha256="x", platform=p, state="queued",
                                  scheduled_at="2026-11-20T03:00:00Z", ref="r")
```

(`_rule`, `GOOD_CAPTION`, `BASE_PROFILE`: reuse or add helpers in the test file; `GOOD_CAPTION` passes hook/link/hashtags/city.)

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/shared/test_caption_rules.py tests/shared/test_social_ledger.py -q` → new tests FAIL.
- [ ] **Step 3: Implement** — extend PLATFORMS; `has_phone` compares the last 10 digits of `phone` against every run of digits in `text` after stripping `+1`, spaces, dots, dashes, parentheses; add the `"phone"` check via the existing `_check` helper; add `"phone": "626-922-0091"` to `social/profiles/el-cuis.json`.
- [ ] **Step 4: Run** `python -m pytest -q` → all pass (existing caption fixtures without `phone` are unaffected).
- [ ] **Step 5: Commit** `feat(social): youtube/gbp ledger platforms and phone caption rule`

---

### Task 2: Platform start snapshot (no backfill)

**Files:**
- Modify: `shared/social_scan.py` (`find_new_videos`)
- Create: `scripts/social-enable-platform.py`
- Test: `tests/shared/test_social_scan.py`, `tests/scripts/test_social_enable_platform.py`

**Interfaces:**
- Produces: profile field `"platformStarts": {"<platform>": {"at": "<ISO UTC>", "excludeSongs": ["<songKey>", ...]}}`.
- Produces: `enable_platform(profile_path: Path, business_key: str, platform: str, *, now: datetime) -> dict` in the script module (returns the updated profile); CLI `python scripts/social-enable-platform.py --business-key el-cuis --platform youtube [--dry-run]`.
- Scan rule: for each platform `p` in `platformStarts`, a candidate whose `songKey` is in `excludeSongs` never has `p` in `missingPlatforms`. If that leaves nothing missing, the file is skipped with reason `"song already scheduled on every platform"` (unmatched) or silently dropped (matched), exactly like today.

- [ ] **Step 1: Write the failing tests**

```python
def test_snapshot_song_never_offered_new_platform(tmp_path):
    profile = _profile(platforms=[..., "youtube"], platformStarts={"youtube": {"at": T0, "excludeSongs": ["reloj"]}})
    ledger = _ledger(reloj={"facebook": "failed", "instagram": "queued", "tiktok": "cancelled"})
    out = find_new_videos(profile, ledger, folder=_folder_with("Reloj Reel.mp4"))
    assert all("youtube" not in c["missingPlatforms"] for c in out["candidates"])

def test_snapshot_song_reexport_not_offered(tmp_path):   # new file, new sha, same song key, possibleRepeat
    ...
    assert out["candidates"][0]["possibleRepeat"] and "youtube" not in out["candidates"][0]["missingPlatforms"]

def test_new_song_after_start_gets_new_platform(tmp_path):
    ...
    assert "youtube" in out["candidates"][0]["missingPlatforms"]

def test_enable_platform_snapshots_ledger_and_pending_queue(fake_s3, tmp_path):
    # ledger has song "reloj"; pending queue has an entry whose sha belongs to song "grito" (ledgerWarning case)
    updated = enable_platform(path, "el-cuis", "youtube", now=NOW)
    assert updated["platformStarts"]["youtube"]["excludeSongs"] == ["grito", "reloj"]
    assert "youtube" in updated["platforms"]

def test_enable_platform_refuses_twice(fake_s3, tmp_path):
    enable_platform(path, "el-cuis", "youtube", now=NOW)
    with pytest.raises(SystemExit): enable_platform(path, "el-cuis", "youtube", now=NOW)
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/shared/test_social_scan.py tests/scripts/test_social_enable_platform.py -q` → FAIL.
- [ ] **Step 3: Implement** — scan: build `excluded = {p: set(v["excludeSongs"]) for p, v in profile.get("platformStarts", {}).items()}` and filter `missing`. Script: load ledger (`social_ledger.load`) + pending/processing queue entries for the business; song keys = ledger song keys ∪ `song_for_sha`/`song_key(fileName)` of queue entries (an entry with no ledger song falls back to its stored `fileName` if present, else is reported and skipped); sorted list; append platform to `platforms`; write the JSON with `indent=2`, `ensure_ascii=False`. `--dry-run` prints the snapshot without writing.
- [ ] **Step 4: Run** `python -m pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat(social): per-platform start snapshot so old songs never reach new platforms`

---

### Task 3: YouTube schedule, list and cancel tools

**Files:**
- Create: `servers/youtube/tools/schedule.py`
- Create: `shared/video_probe.py`
- Create: `servers/youtube/local_stdio.py` (copy the TikTok one; needs `ADS_MCP_MEDIA_BUCKET` plus AWS chain for the youtube secret)
- Modify: `servers/youtube/mcp_server.py` (register 3 tools; update `instructions`)
- Test: `tests/youtube/test_youtube_schedule.py`, `tests/shared/test_video_probe.py`

**Interfaces:**
- Produces: `video_probe.probe(path: str) -> dict | None` → `{"width": int, "height": int, "seconds": float}` via `ffprobe` (`shutil.which`); `None` if ffprobe is missing or fails. `video_probe.extract_frame(path: str, out_path: str, *, at_seconds: float) -> str` (ffmpeg `-ss <t> -frames:v 1 -vf scale=1200:-2 -q:v 3`), raises `RuntimeError` on failure.
- Produces tools (FastMCP names → functions in `schedule.py`):
  - `youtube_schedule_video(business_key, file_path, scheduled_time, title, description, approval_id=None)` → `schedule_video(req, request_id)`
  - `youtube_list_scheduled(business_key)` → `list_scheduled(req, request_id)`: queued YouTube ledger records joined with `videos.list(part="status,snippet")`.
  - `youtube_cancel_scheduled(business_key, video_id, approval_id=None)` → `cancel_scheduled(req, request_id)`: only if the video is still `private` with `publishAt` in the future; `videos.delete`, move its verify queue entry to `cancelled`, `social_ledger.mark_cancelled(..., platform="youtube", ref=video_id)`.
- Execute result: `{"videoId": str, "url": "https://youtu.be/<id>", "verifyQueueKey": str, "short": bool | None}`; ledger ref = `videoId`.
- Verify queue entry (consumed by Task 4): `{"platform": "youtube", "businessKey", "scheduledAt": <slot + 60 min>, "videoSha256", "videoId", "publishAt": <slot>}`. `scheduledAt` is slot+60 min so the runner checks after YouTube has had time to flip it public.

- [ ] **Step 1: Write the failing tests** (fake YouTube client via monkeypatching `schedule._youtube`, a callable returning an object whose `videos().insert/list/delete` record calls)

```python
def test_dry_run_checks_title_phone_and_window(fake_s3, social_profile_dir):
    r = _dry(title="Reloj | Mariachi en vivo para tu fiesta en Los Ángeles", description=CAPTION_WITH_PHONE)
    assert r["mode"] == "dry-run" and r["approval"] and {c["rule"] for c in r["ruleChecks"]} >= {"youtube-title", "phone", "hook"}

@pytest.mark.parametrize("title", ["", "x" * 101, "Mariachi | Reloj", "Reloj <live>"])
def test_bad_title_blocks(title, fake_s3, social_profile_dir):
    assert _dry(title=title)["approval"] is None

def test_shorts_flag(fake_s3, social_profile_dir, monkeypatch):
    monkeypatch.setattr(video_probe, "probe", lambda p: {"width": 1080, "height": 1920, "seconds": 204.6})
    assert _dry()["data"]["short"] is False   # >180 s posts as regular video
    monkeypatch.setattr(video_probe, "probe", lambda p: None)
    assert _dry()["data"]["short"] is None    # ffprobe missing: still allowed

def test_execute_uploads_private_with_publish_at_and_logs(fake_s3, social_profile_dir, fake_yt):
    r = _execute()
    body = fake_yt.inserted[0]["body"]
    assert body["status"] == {"privacyStatus": "private", "publishAt": "2026-11-20T03:00:00Z", "selfDeclaredMadeForKids": False}
    assert body["snippet"]["categoryId"] == "10"
    assert _logged("youtube")["ref"] == r["data"]["videoId"]
    assert _queue("pending")[0]["platform"] == "youtube"

def test_execute_ledger_failure_returns_warning_and_keeps_verify_entry(...):   # Review Focus 2
    ...
    assert "ledgerWarning" in r["data"] and len(_queue("pending")) == 1

def test_rejected_upload_marks_failed(...):   # status.uploadStatus == "rejected" or "failed"
    ...
    assert _logged("youtube")["state"] == "failed"

def test_cancel_refuses_after_publish_time(...):
    ...
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/youtube tests/shared/test_video_probe.py -q` → FAIL.
- [ ] **Step 3: Implement** — reuse `scheduling.parse_schedule_time`, `schedule_window_check`, `video_source`, `file_preview`, `bound_args`, `write_flow`; `check_caption(profile, description)` for the description; title check as `rule="youtube-title"` (song name = the candidate's display name: file stem minus trailing Reel/Post/clip/(n), compared case- and accent-insensitively with `caption_rules._normalize`). Upload with `MediaFileUpload(path, chunksize=-1, resumable=True)` and `next_chunk()` loop; after insert, `videos.list(part="status")` once: `uploadStatus` in `{"rejected","failed"}` → raise `AdsMcpError(retryable=False)` so `run_and_mark` logs `failed` (delete the bad video best-effort). The insert+verify-enqueue is the `call` inside `run_and_mark(platform="youtube", ref_of=lambda r: r["videoId"])`. Reuse `read._load_config`/credential building; `_upstream` for API errors. Description ≤5,000 chars and no `<`/`>` (YouTube rejects them): blocking `invalid`.
- [ ] **Step 4: Run** `python -m pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat(youtube): scheduled uploads with dry run, list and cancel`

---

### Task 4: Runner verifies YouTube went public

**Files:**
- Modify: `scripts/run-scheduled-social.py` (new publisher, register in `main`, label in `_handle_processing`)
- Modify: `scripts/deploy.sh:34` (install `servers/youtube/requirements.txt` on EC2)
- Test: `tests/scripts/test_run_scheduled_social.py`

**Interfaces:**
- Consumes: Task 3 verify entry.
- Produces: `verify_youtube_entry(entry: dict) -> dict` returns `{"videoId", "privacyStatus": "public"}`. With the existing `MAX_ATTEMPTS = 3` and the 30-min timer, checks happen at about slot +60, +90 and +120 min. Raises:
  - `AdsMcpError(retryable=True)` when still private (or a transient API error) and `int(entry["attempts"]) + 1 < MAX_ATTEMPTS`.
  - `UnknownOutcome(Exception)` (new class in the runner) when the video is missing, or still private on the last attempt. `_handle_pending` catches it before the generic `AdsMcpError` path and calls `_fail(..., definite=False)` with `lastError` "YouTube video <id> is not public after its slot; check YouTube Studio". It must never go through the retry-exhausted `_fail(definite=True)` path, which would mark the log failed and re-offer a second upload.
- Shared helper `youtube_status(business_key: str, video_id: str) -> dict | None` in `servers/youtube/tools/schedule.py`, reused by Task 6 (`None` = video not found).

- [ ] **Step 1: Write the failing tests**

```python
def test_youtube_public_marks_done(fake_s3, social_profile_dir, monkeypatch): ...  # done, ledger stays queued
def test_youtube_still_private_retries_then_fails_without_ledger(fake_s3, social_profile_dir, monkeypatch):  # Review Focus 3
    ...
    assert len(_states(fake_s3)["failed"]) == 1 and _logged("youtube")["state"] == "queued"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — import lazily inside the publisher so the runner still starts if YouTube libs are missing; `_handle_processing` label map gains `"youtube": "YouTube Studio"`, `"gbp": "the Google Business Profile"`.
- [ ] **Step 4: Run** `python -m pytest -q` → pass.
- [ ] **Step 5: Commit** `feat(social): runner confirms scheduled YouTube videos went public`

---

### Task 5: Connect El Cuis GBP + the GBP schedule tool

**Files:**
- Create: `scripts/gbp-auth.py`
- Create: `servers/gbp/tools/social_post.py` (new `tools/` package in the gbp server)
- Modify: `servers/gbp/mcp_server.py` (register `gbp_schedule_social_post`, `gbp_list_social_posts`, `gbp_cancel_social_post`)
- Modify: `shared/media_queue.py` (add `put_image`)
- Test: `tests/gbp/test_gbp_social_post.py`, `tests/scripts/test_gbp_auth.py`, `tests/shared/test_media_queue.py`

**Interfaces:**
- `scripts/gbp-auth.py --business-key el-cuis [--port 8080]`: OAuth (`https://www.googleapis.com/auth/business.manage`) with `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` (the project GQ already uses); lists accounts → locations (title + primary phone), user picks a number; writes `/ads-mcp/<bk>/gbp/config` via `secrets.put_secret` with the five config keys; prints a WARNING if the chosen location's primary phone digits ≠ profile `phone`. Testable core: `build_config(...) -> dict`, `phone_matches(listing_phone: str | None, phone: str) -> bool` (reuse `caption_rules.has_phone`).
- `media_queue.put_image(*, business_key: str, file_path: str, sha256: str) -> str` → key `images/<bk>/<sha>.jpg`, ContentType `image/jpeg`, skip if exists.
- Tool `gbp_schedule_social_post(business_key, file_path, scheduled_time, summary, frame_seconds=None, approval_id=None)` → `schedule_social_post(req, request_id)`:
  - Dry run: `video_probe.extract_frame` at `frame_seconds` or 25% of duration into `%LOCALAPPDATA%/ads-mcp/frames/<videoSha>-<t>.jpg`; preview carries `framePath` (the skill shows it); ffprobe/ffmpeg missing or failing → blocking rule `gbp-frame` with message "ffmpeg is needed to make the GBP photo".
  - Rule checks: `hook` (first line, reuse `check_caption` hook logic via a small exported helper `caption_rules.check_hook(profile, text)`), `gbp-no-phone` (blocking if `has_phone(summary, profile["phone"])` or any 10-digit run), `gbp-length` (`len(summary) + LINK_LINE_BUDGET` ≤ 1500, `LINK_LINE_BUDGET = 120`), window check.
  - Execute: `put_image` + `enqueue({"platform": "gbp", "businessKey", "scheduledAt", "videoSha256", "imageKey", "summary", "songKey"})` inside `run_and_mark(platform="gbp", ref_of=entryId)`. `bound_args` binds the video sha AND `frame_seconds`.
- `gbp_list_social_posts` / `gbp_cancel_social_post(entry_id)`: same as `servers/tiktok/tools/schedule.py` `list_scheduled`/`cancel_scheduled` with `PLATFORM="gbp"` (factor nothing; copy the ~40 lines).
- The existing GQ GBP queue (`scheduled_posts.json`, `gbp_create_post`) is untouched.

- [ ] **Step 1: Write the failing tests** — dry run returns `framePath` and an approval; summary containing `626-922-0091` blocks on `gbp-no-phone`; 1,400-char summary blocks on `gbp-length`; ffmpeg missing blocks on `gbp-frame` (Review Focus 5); execute puts `images/el-cuis/<sha>.jpg`, enqueues one `gbp` entry, ledger `gbp` queued with ref = entryId; `phone_matches("(626) 922-0091", "626-922-0091")` true, `phone_matches(None, ...)` false.
- [ ] **Step 2: Run** `python -m pytest tests/gbp tests/scripts/test_gbp_auth.py tests/shared/test_media_queue.py -q` → FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** `python -m pytest -q` → pass.
- [ ] **Step 5: Commit** `feat(gbp): El Cuis GBP connect script and scheduled social post tool`

---

### Task 6: Runner publishes GBP posts

**Files:**
- Modify: `servers/gbp/gbp_client.py` (`create_post` gains `media_url: str | None = None` → `body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": media_url}]`)
- Modify: `scripts/run-scheduled-social.py` (`publish_gbp_entry`, register in `main`)
- Test: `tests/scripts/test_run_scheduled_social.py`, `tests/gbp/test_gbp_client.py`

**Interfaces:**
- Consumes: Task 5 `gbp` entry; Task 4 `youtube_status`.
- `publish_gbp_entry(entry) -> dict` returns `{"resourceName", "searchUrl", "linkLine"}`:
  - Link line: look up the song's ledger `youtube` record (`social_ledger.load(bk)["songs"][entry["songKey"]]["platforms"].get("youtube")`); if state `queued` and `youtube_status(...)["privacyStatus"] == "public"` → `"▶️ Mira el video completo en YouTube: https://youtu.be/<id>"`; otherwise `"Más videos y reservaciones: https://mariachielcuis.com/"`. Any error in the lookup → website line (never fail the post over the link).
  - `create_post(config, summary=entry["summary"] + "\n\n" + link_line, topic_type="STANDARD", call_to_action_type="CALL", media_url=media_queue.presigned_get(entry["imageKey"]), language_code="es")`.
  - GBP 4xx → `AdsMcpError(retryable=False)` (definite → ledger `failed`, re-offered next run); 5xx/timeout → retryable.

- [ ] **Step 1: Write the failing tests** — public YouTube → summary ends with the youtu.be line and CTA is CALL with one PHOTO media; YouTube private / no record / lookup raises → website line (Review Focus 4); GBP 400 → entry failed and ledger `gbp` failed.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m pytest -q` → pass.
- [ ] **Step 5: Commit** `feat(social): runner publishes El Cuis GBP posts with a Call button`

---

### Task 7: Skill, docs and switch-on

**Files:**
- Modify: `.claude/skills/schedule-social/SKILL.md`
- Modify: `README.md` (YouTube + GBP setup and MCP registration: `claude mcp add youtube -- python servers/youtube/local_stdio.py`, gbp server env needs `ADS_MCP_MEDIA_BUCKET`)
- Modify: `social/profiles/el-cuis.json` via the Task 2 script (done at ship time, see Step 3)

**Skill changes (exact behavior):**
- Description/intro: Facebook, Instagram, TikTok, YouTube and GBP.
- Step 7 caption format gains a line `📞 626-922-0091` after the city line; same caption on FB/IG/TikTok/YouTube description.
- New step 7b: per video write the YouTube title (`<Song name> | <Spanish hook>`, ≤100) and the GBP summary (Spanish hook + 1–2 Spanish lines about booking for the event/city + one short English line; no phone, no link; the runner adds the link).
- Step 8 adds: `youtube_schedule_video(business_key, file_path, scheduled_time, title, description=<caption>)` and `gbp_schedule_social_post(business_key, file_path, scheduled_time, summary)`; show the GBP frame image (Read `framePath`) and offer to pick another second if it looks bad (re-dry-run with `frame_seconds`).
- Step 9 table adds YouTube title, Short / regular video, GBP frame + summary, phone rule per platform.
- Step 10 execution order per post: Facebook, Instagram, TikTok, YouTube, GBP.
- Step 12 adds: YouTube goes public by itself at the slot; GBP posts within 30 min of the slot with the YouTube link if it is live.

- [ ] **Step 1: Edit skill and README as above.**
- [ ] **Step 2: Run** `python -m pytest -q` → pass; commit `docs(social): schedule-social covers YouTube and GBP`.
- [ ] **Step 3: Switch-on (after merge, with the user, before their next `/schedule-social`):** user runs `python scripts/youtube-auth.py --business-key el-cuis` and `python scripts/gbp-auth.py --business-key el-cuis`; deploy runner to EC2; then `python scripts/social-enable-platform.py --business-key el-cuis --platform youtube --dry-run`, show the snapshot, then without `--dry-run`; same for `gbp`; commit the profile.
