# Social Posting Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Schedule new videos" turns new files in the El Cuis Ready folder into approved, rule-checked, bilingual posts queued to Facebook, Instagram and TikTok in fixed weekly slots.

**Architecture:** A per-business social profile (committed JSON) drives three new shared modules: caption rules, a posting log in S3, and folder scan + slot planning. The four existing schedule tools enforce the caption rules and write the posting log on execute. A new local stdio `social` MCP server exposes the read-side (new videos, open slots, posting log). A new read-only Meta organic insights tool feeds the hour choice. A Claude Code skill is the playbook tying it together.

**Tech Stack:** Python 3.12, FastMCP, boto3 (S3), requests via `shared.meta_ads_client._graph_request`, pytest with `tests/fake_s3.py`.

**Spec:** `docs/superpowers/specs/2026-11-10-social-posting-workflow-design.md`

## Global Constraints

- Business key `el-cuis`; website domain `mariachielcuis.com`; links must be `https://`, `http://` rejected.
- Service cities (exact list): Beverly Hills, Whittier, Downey, Pasadena, La Habra, Altadena, West Hollywood, Hollywood, Santa Monica, Compton.
- Slots (America/Los_Angeles): Mon–Fri one slot in 18:00–21:00 on :00/:30, default 19:00; Sat/Sun 10:00 and 15:00. Horizon 7 days. First slot ≥ 60 min from now.
- Baseline cutoff: files created before `2026-11-10T19:00:00-08:00` are never posted.
- Ready folder: `C:\Users\cesar\Desktop\Mariachi\Assets\Ready`.
- Platforms (log keys): `facebook`, `instagram`, `tiktok`. Same video, same slot on all three.
- Caption: bilingual, Spanish first; 8–12 hashtags; hook ≤ 150 chars; Instagram ≤ 30 hashtags, ≤ 2,200 chars.
- Approval store, its 15-minute TTL and the dry-run → approvalId → execute contract are **not changed**.
- Caption rules and log writes apply only to businesses that have a social profile; every other business behaves exactly as today.
- No credentials in code. Profile JSON holds no secrets.
- Baseline is derived from the cutoff on every scan rather than written to the log on the first run. The effect is the same, and the ~11 GB of baseline files never get hashed.
- Real location tags (Instagram `location_id`, Facebook place) are deferred. The city appears in the caption text, which the rules enforce. The spec leaves feasibility to the builder.

## Review Focus

1. **A video over 300 MB** (Instagram Reels limit; many current files are 300–520 MB) → scan marks it `instagramTooLarge`, the skill tells the user to re-export instead of retrying Instagram every run. Test in Task 4.
2. **Two cuts / renamed copies of one song** (`6 pies abajo Post` vs `6piesAbajo Reel`; `Caminos de Guanajuato2`) → same song key; `17anos` and `20 Anos` stay different. Test in Task 3.
3. **A file created before the cutoff but modified later** → still baseline (creation time, not mtime). Test in Task 4.
4. **Slot already taken by a post scheduled outside the workflow** (e.g. tonight's TikTok test in the S3 queue) → slot is not offered. Test in Task 4.
5. **Caption with the city in a different case/accent or inside a hashtag** (`#downey`, `DOWNEY`) → city rule passes; a city outside the list (`Riverside`) alone fails. Test in Task 1.

---

### Task 1: Social profile and caption rules

**Files:**
- Create: `social/profiles/el-cuis.json`
- Create: `shared/social_profile.py`
- Create: `shared/caption_rules.py`
- Modify: `tests/conftest.py` (autouse fixture)
- Test: `tests/shared/test_caption_rules.py`

**Interfaces:**
- Produces:
  - `social_profile.load_profile(business_key: str) -> dict | None` — reads `<ADS_MCP_SOCIAL_PROFILES_DIR or repo/social/profiles>/<business_key>.json`; `None` if missing.
  - `caption_rules.check_caption(profile: dict, caption: str | None) -> list[dict]` — returns `build_rule_check(...)` dicts with rules `caption-hook`, `caption-https-link`, `caption-hashtags`, `caption-city`; each `severity="blocking"` when failed, `"info"` when passed.

- [ ] **Step 1: Write the profile file** with exactly:

```json
{
  "businessKey": "el-cuis",
  "websiteDomain": "mariachielcuis.com",
  "serviceCities": ["Beverly Hills", "Whittier", "Downey", "Pasadena", "La Habra", "Altadena", "West Hollywood", "Hollywood", "Santa Monica", "Compton"],
  "readyFolder": "C:\\Users\\cesar\\Desktop\\Mariachi\\Assets\\Ready",
  "baselineCutoff": "2026-11-10T19:00:00-08:00",
  "hookMaxChars": 150,
  "alwaysHashtags": ["#mariachi", "#mariachiloscangeles", "#mariachielcuis"],
  "slots": {"weekdayWindow": ["18:00", "21:00"], "weekdayDefault": "19:00", "weekend": ["10:00", "15:00"], "horizonDays": 7, "minLeadMinutes": 60},
  "platforms": ["facebook", "instagram", "tiktok"]
}
```

- [ ] **Step 2: Add autouse fixture in `tests/conftest.py`** that sets `ADS_MCP_SOCIAL_PROFILES_DIR` to an empty `tmp_path` subdir, so existing tests (which use `el-cuis` with short captions) see no profile. Tests that need it copy the real profile in via a `social_profile_dir` fixture.

- [ ] **Step 3: Write failing tests** in `tests/shared/test_caption_rules.py`:

```python
GOOD = ("¿Quinceañera en Downey? Así suena 'Hermoso Cariño' en vivo 🎺\nHagamos tu celebración inolvidable.\n\n"
        "Quinceañera in Downey? This is 'Hermoso Cariño' live 🎺\n\n📍 Downey · Whittier\n"
        "🔗 https://mariachielcuis.com/quinceaneras\n\n#mariachi #quinceañera #downey")

def failed(checks): return {c["rule"] for c in checks if not c["passed"]}

def test_good_caption_passes(profile): assert failed(check_caption(profile, GOOD)) == set()
def test_missing_caption_fails_all(profile): assert failed(check_caption(profile, None)) == {"caption-hook", "caption-https-link", "caption-hashtags", "caption-city"}
def test_http_link_rejected(profile): assert "caption-https-link" in failed(check_caption(profile, GOOD.replace("https://", "http://")))
def test_other_domain_rejected(profile): assert "caption-https-link" in failed(check_caption(profile, GOOD.replace("mariachielcuis.com", "example.com")))
def test_www_subdomain_ok(profile): assert failed(check_caption(profile, GOOD.replace("https://", "https://www."))) == set()
def test_no_hashtags_fails(profile): assert failed(check_caption(profile, GOOD.split("\n\n#")[0])) == {"caption-hashtags"}
def test_hook_too_long(profile): assert "caption-hook" in failed(check_caption(profile, "x" * 151 + "\n" + GOOD))
def test_hook_cannot_be_hashtag_or_link(profile):
    assert "caption-hook" in failed(check_caption(profile, "#mariachi\n" + GOOD))
    assert "caption-hook" in failed(check_caption(profile, "https://mariachielcuis.com\n" + GOOD))
def test_city_case_accent_and_hashtag(profile):
    base = GOOD.replace("Downey", "X").replace("Whittier", "X").replace("#downey", "")
    assert "caption-city" in failed(check_caption(profile, base + " Riverside"))
    assert failed(check_caption(profile, base + " #downey")) == set()
    assert failed(check_caption(profile, base + " DOWNEY")) == set()
    assert failed(check_caption(profile, base + " #santamonica")) == set()
```

- [ ] **Step 4: Run** `.venv/Scripts/python -m pytest tests/shared/test_caption_rules.py -v` → FAIL (module missing).

- [ ] **Step 5: Implement** `shared/social_profile.py` and `shared/caption_rules.py`. Hook = first non-empty line, stripped, 1..`hookMaxChars` chars, not starting with `#` or `http`. Link: regex `https://(www\.)?<domain>(/\S*)?`; fail if any `http://` URL is present. City matching: casefold + strip accents (`unicodedata` NFKD) + remove spaces on both caption and city names, substring match (so `#santamonica` matches "Santa Monica"). Messages name what is missing.

- [ ] **Step 6: Run** the test file → PASS. Run full suite `.venv/Scripts/python -m pytest -q` → all pass.

- [ ] **Step 7: Commit** `feat(social): El Cuis social profile and caption rules`

---

### Task 2: Enforce caption rules in the four schedule tools

**Files:**
- Modify: `servers/meta-ads/tools/instagram.py` (`schedule_instagram_reel`)
- Modify: `servers/meta-ads/tools/publish.py` (`schedule_page_reel`, `schedule_page_video`; caption field is `description`)
- Modify: `servers/tiktok/tools/schedule.py` (`schedule_inbox_video`)
- Test: `tests/meta_ads/test_instagram.py`, `tests/meta_ads/test_publish.py`, `tests/tiktok/test_schedule.py`

**Interfaces:**
- Consumes: `load_profile`, `check_caption` (Task 1).
- Produces: each tool, when `load_profile(businessKey)` is not None, appends `check_caption(profile, caption)` to `rule_checks` (so a failure blocks in dry run and execute via `write_flow`). No profile → unchanged behavior.

- [ ] **Step 1: Write failing tests**, one pair per tool (4 tools): with `social_profile_dir` fixture active, `test_<tool>_blocks_caption_without_link` (dry run returns no `approvalId`, summary starts `Blocked by business rules`, rule `caption-https-link` failed) and `test_<tool>_passes_good_caption` (approvalId issued, all four caption rules passed). Reuse the `GOOD` caption from Task 1 (move it to `tests/social_fixtures.py`).
- [ ] **Step 2: Run** those tests → FAIL.
- [ ] **Step 3: Implement** the append in each tool. TikTok: caption stays optional only when no profile exists.
- [ ] **Step 4: Run** the four test files and full suite → PASS (existing tests unaffected thanks to the autouse fixture).
- [ ] **Step 5: Commit** `feat(social): schedule tools enforce caption rules for profiled businesses`

---

### Task 3: Posting log and song keys

**Files:**
- Create: `shared/social_ledger.py`
- Modify: the four schedule tools' `execute()` closures; `cancel_scheduled_social` (instagram.py) and `cancel_scheduled` (tiktok schedule.py)
- Test: `tests/shared/test_social_ledger.py`, plus additions to the three tool test files

**Interfaces:**
- Produces:
  - `social_ledger.song_key(file_name: str) -> str`
  - `social_ledger.load(business_key: str) -> dict` — `{"songs": {song_key: {"files": [sha...], "platforms": {platform: {"state", "scheduledAt", "sha256", "ref", "updatedAt"}}}}}`; empty `{"songs": {}}` if absent.
  - `social_ledger.mark(business_key: str, *, song_key: str, sha256: str, platform: str, state: str, scheduled_at: str | None, ref: str | None) -> None` — states `queued`, `failed`, `cancelled`.
  - S3 key `ledger/<business_key>.json` in the media bucket; read-modify-write guarded by the same conditional-write approach `media_queue` uses (ETag `IfMatch`, retry up to 3 times).

- [ ] **Step 1: Write failing tests** for `song_key`:

```python
@pytest.mark.parametrize("a,b", [
    ("6 pies abajo Post.mp4", "6piesAbajo Reel.mp4"),
    ("17anos Reel.mp4", "17anos Reel (1).mp4"),
    ("Caminos de Guanajuato2 Reel.mp4", "Caminos de Guanajuato3 Reel.mp4"),
    ("Hermoso Carino Post.mp4", "Hermoso Cariño Reel.mov"),
])
def test_same_song(a, b): assert song_key(a) == song_key(b)

def test_different_songs(): assert song_key("17anos Reel.mp4") != song_key("20 Anos Reel.mp4")
```

  and for the log (with `fake_s3`): `test_mark_then_load_roundtrip`, `test_mark_keeps_other_platforms`, `test_mark_appends_sha_once`.

  Normalization: strip extension, casefold, strip accents, remove `(n)` copy suffix, remove trailing words `reel|post|clip|ads`, remove trailing digits of the last word, then drop all non-alphanumerics.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `shared/social_ledger.py`.
- [ ] **Step 4: Tool wiring tests:** with profile active, `test_<tool>_execute_marks_ledger_queued` (log has the platform `queued` with scheduledAt and ref = queue entry id or Facebook video id) for all four tools; `test_cancel_marks_ledger_cancelled` for the Instagram and TikTok cancel tools. Without a profile, no `ledger/` object is written.
- [ ] **Step 5: Implement** wiring: after the platform call succeeds inside `execute()`, call `mark(...)` with `song_key(os.path.basename(filePath))`. If the platform call raises, call `mark(..., state="failed")` and re-raise. Cancel tools look up the song by the entry's `videoSha256`.
- [ ] **Step 6: Run** tests and full suite → PASS.
- [ ] **Step 7: Commit** `feat(social): S3 posting log written by schedule tools`

---

### Task 4: Folder scan, slots, and the `social` MCP server

**Files:**
- Create: `shared/social_scan.py`, `shared/social_slots.py`
- Create: `servers/social/mcp_server.py`, `servers/social/local_stdio.py` (copy the tiktok `local_stdio.py` pattern), `servers/social/tools/__init__.py`, `servers/social/tools/read.py`, `servers/social/requirements.txt`
- Test: `tests/shared/test_social_scan.py`, `tests/shared/test_social_slots.py`, `tests/social/test_read.py`

**Interfaces:**
- Consumes: `load_profile`, `social_ledger.load/song_key`, `media_queue.list_entries`, `scheduling.sha256_file`, `scheduling.SCHEDULE_TZ`.
- Produces:
  - `social_scan.find_new_videos(profile: dict, ledger: dict, *, folder: str | None = None) -> dict` → `{"candidates": [{"songKey", "file", "cut": "reel"|"post", "createdAt", "bytes", "sha256", "missingPlatforms": [...], "instagramTooLarge": bool, "possibleRepeat": bool}], "baselineCount": int, "skipped": [{"file", "reason"}]}`; oldest `createdAt` first.
  - `social_slots.open_slots(profile: dict, *, now: datetime, taken: list[datetime], weekday_time: str | None = None) -> list[datetime]` (aware UTC).
  - MCP tools (all read-only): `social_find_new_videos(business_key, folder=None)`, `social_next_open_slots(business_key, weekday_time=None)`, `social_get_posting_log(business_key)`.

- [ ] **Step 1: Write failing scan tests** (use `tmp_path` files; monkeypatch a `_created_at(path)` helper that returns `st_birthtime` if present else `st_ctime`):
  - `test_files_before_cutoff_are_baseline` — created 2026-11-01 → not a candidate, counted in `baselineCount`, **and its file is not hashed** (assert `sha256_file` not called for it).
  - `test_created_before_cutoff_modified_after_is_baseline` (mtime later, creation earlier).
  - `test_reel_preferred_over_post` — `X Post.mp4` + `X Reel.mp4` after cutoff → one candidate, `cut == "reel"`; only `Post` → `cut == "post"`.
  - `test_song_done_on_all_platforms_is_skipped`; `test_partial_song_lists_missing_platforms` (ledger has instagram+facebook queued, tiktok failed → `missingPlatforms == ["tiktok"]`).
  - `test_new_cut_of_baseline_song_flagged_possible_repeat` (`Reloj Reel.mp4` baseline, `Reloj2 Reel.mp4` new → candidate with `possibleRepeat=True`).
  - `test_over_300mb_flags_instagram` — monkeypatch size → `instagramTooLarge=True`.
  - `test_non_video_files_ignored` (`.png`).
  - `test_order_oldest_first`.
- [ ] **Step 2: Write failing slot tests** (`now` = Mon 2026-11-16 12:00 PST):
  - `test_week_has_nine_slots` → 9 slots: Mon–Fri 19:00, Sat 10:00 & 15:00, Sun 10:00 & 15:00 within 7 days.
  - `test_weekday_time_override` — `"20:30"` → weekday slots at 20:30.
  - `test_weekday_time_outside_window_rejected` — `"17:30"` and `"21:30"` and `"19:15"` raise `ValueError`.
  - `test_min_lead` — `now` = Mon 18:30 → Monday 19:00 excluded (less than 60 min).
  - `test_taken_slot_excluded` — a taken time equal to Tue 19:00 removes it; a taken time within ±30 min of a slot also removes it.
- [ ] **Step 3: Run** → FAIL. **Step 4: Implement** both modules.
- [ ] **Step 5: Write failing server tool tests** (`tests/social/test_read.py`, `fake_s3`, profile dir): `social_next_open_slots` treats pending/processing S3 queue entries for the business and ledger `queued` entries as taken (Review Focus 4); `social_find_new_videos` returns the scan result; unknown business (no profile) → `REQUEST_INVALID` "no social profile".
- [ ] **Step 6: Implement** the server (FastMCP, `_run_tool` pattern from `servers/tiktok/mcp_server.py`; instructions string: "Read-only helpers for the schedule-social skill."). **Step 7: Run** tests + full suite → PASS.
- [ ] **Step 8: Commit** `feat(social): folder scan, slot planner, and social MCP server`

---

### Task 5: Meta organic insights read tool

**Files:**
- Modify: `shared/meta_ads_client.py` (new functions), `servers/meta-ads/tools/read.py`, `servers/meta-ads/mcp_server.py`
- Test: `tests/meta_ads/test_read.py`

**Interfaces:**
- Produces:
  - `meta_ads_client.get_instagram_media_insights(config, *, ig_user_id: str, since: datetime, tool=None) -> list[dict]` — `GET {ig}/media?fields=id,caption,media_product_type,timestamp,permalink,like_count,comments_count`, paged until older than `since`; per media `GET {id}/insights?metric=reach,views,saved,shares,total_interactions`.
  - `meta_ads_client.get_instagram_online_followers(config, *, ig_user_id: str, tool=None) -> dict[int, int] | None` — `GET {ig}/insights?metric=online_followers&period=lifetime`; UTC hour → count, averaged over returned days; `None` if the metric errors.
  - `meta_ads_client.get_page_post_insights(config, *, page_id: str, since: datetime, tool=None) -> list[dict]` — page access token; `GET {page}/published_posts?fields=id,message,created_time,permalink_url` then per post reach (`post_impressions_unique`) and reactions/comments/shares from fields.
  - Tool `get_organic_insights(request)` → data `{"instagram": {"posts": [...], "audienceByHourPacific": {hour: n} | null}, "facebook": {"posts": [...]}, "recommendedWeekdayTime": "HH:MM", "recommendationReason": str, "warnings": [...]}`; MCP name `meta_ads_get_organic_insights(business_key, days: int = 30)`.

- [ ] **Step 1: Write failing tests** (monkeypatch the three client functions):
  - `test_post_rows_normalized` — numbers coerced to int, `publishedPacific` added, caption first line as `hook`.
  - `test_recommends_peak_half_hour_in_window` — audience peaks at 20:00 PT by ≥ 10% over 19:00 → `"20:00"`.
  - `test_recommends_default_when_flat_or_missing` — peak < 10% better, or `None` → `"19:00"` with reason.
  - `test_metric_error_becomes_warning` — client raises `AdsMcpError` for Facebook → Instagram data still returned, `warnings` non-empty.
  Recommendation only considers hours 18, 19, 20 (slots 18:00–20:30 with a 21:00 end); choose the hour, use `:00`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + full suite → PASS.
- [ ] **Step 5: Live smoke check** (read-only): call `meta_ads_get_organic_insights` for `el-cuis` after the meta-ads server restarts; if permission errors appear (e.g. `instagram_manage_insights`, `read_insights`), record them in `docs/ONBOARDING_FOLLOW_UPS.md` and tell the user — do not change app permissions.
- [ ] **Step 6: Commit** `feat(meta-ads): read-only organic insights tool`

---

### Task 6: Connect GA4, Search Console and social servers to Claude Code

**Files:** none in repo except README section (Task 8). Uses `claude mcp add`.

- [ ] **Step 1:** Register, matching the existing google-ads local registration:
  - `claude mcp add analytics -s local -- C:/Users/cesar/Code/ads-mcp/.venv/Scripts/python.exe C:/Users/cesar/Code/ads-mcp/servers/analytics/mcp_server.py`
  - `claude mcp add search-console -s local -- C:/Users/cesar/Code/ads-mcp/.venv/Scripts/python.exe C:/Users/cesar/Code/ads-mcp/servers/search-console/mcp_server.py`
  - `claude mcp add social -s local -- C:/Users/cesar/Code/ads-mcp/.venv/Scripts/python.exe C:/Users/cesar/Code/ads-mcp/servers/social/local_stdio.py`
- [ ] **Step 2:** `claude mcp list` → all three `✔ Connected`. If analytics/search-console need a `local_stdio.py` wrapper for env like meta-ads does, add one following `servers/meta-ads/local_stdio.py`.
- [ ] **Step 3:** After the user restarts Claude Code, smoke-read `analytics_get_top_pages(el-cuis)`, `search_console_get_search_performance(el-cuis, dimension="query")`, `google_ads_get_search_terms_report(el-cuis)`; report any that fail (missing El Cuis config) without fixing credentials.

---

### Task 7: The `schedule-social` skill

**Files:**
- Create: `.claude/skills/schedule-social/SKILL.md`

**Interfaces:**
- Consumes: `social_find_new_videos`, `social_next_open_slots`, `social_get_posting_log`, `meta_ads_get_organic_insights`, `analytics_get_top_pages`, `search_console_get_search_performance`, `google_ads_get_search_terms_report`, web search, the four schedule tools, `servers/content-agent/brands/el-cuis.md`.

- [ ] **Step 1: Write the skill.** Frontmatter `name: schedule-social`, description triggering on "schedule new videos" / "schedule social posts". Body, as an ordered checklist:
  1. Read the brand brief and `social/profiles/<business>.json`.
  2. `social_find_new_videos`. Nothing new → say so and stop. List `skipped`, `instagramTooLarge` (ask for a re-export under 300 MB; schedule Facebook + TikTok only if the user says so), and `possibleRepeat` (ask include/skip).
  3. `meta_ads_get_organic_insights` → weekday time (`recommendedWeekdayTime`) and what hooks/topics/cities performed.
  4. `social_next_open_slots(weekday_time=...)`; assign candidates oldest-first to slots; extras wait for next run.
  5. Demand data: Search Console queries/pages (last 28 days), Google Ads search terms, GA4 top pages → per video pick event topic, 1–2 service cities (no city two posts in a row), and a real `https://mariachielcuis.com/...` page (must appear in GA4 or Search Console page data).
  6. Web search for current mariachi / LA event hashtags; pick 8–12 total incl. `alwaysHashtags`, one mixed block.
  7. Write each caption in the spec's bilingual format; follow brand guardrails (no pricing, availability or unconfirmed claims).
  8. Dry-run every post on every missing platform: `meta_ads_schedule_page_reel` (Facebook; `description` = caption), `meta_ads_schedule_instagram_reel`, `tiktok_schedule_inbox_video`. Show the batch as a table per video: slot, file, platforms, rule checks, full caption.
  9. Wait for one explicit batch approval.
  10. Per post: fresh dry run with identical args → if args or rule checks differ from what was shown, or anything is blocked, stop and ask. Else execute immediately with the fresh approvalId.
  11. On a platform error: keep the others, report it (the log marks it `failed`; next run offers it again).
  12. Finish with: what was queued per platform, the TikTok captions to paste, and the reminder that TikTok drafts arrive in the inbox up to 30 min after the slot.
  Also state: never post a baseline file; never edit a scheduled post without a new dry run; never change campaign settings.
- [ ] **Step 2: Verify** in a fresh Claude Code session that `/schedule-social` is listed. Then run it with a dry-run-only instruction ("stop before approval") against a test file copied into the Ready folder, and confirm the batch preview shows all four caption rule checks passing on all three platforms. Delete the test file afterwards.
- [ ] **Step 3: Commit** `feat(social): schedule-social skill`

---

### Task 8: Docs and final verification

**Files:**
- Modify: `README.md` (section "Social posting workflow": profile file, servers to register, `/schedule-social`, the 300 MB Instagram note)
- Modify: `CLAUDE.md` — add El Cuis under Businesses only if the user approves editing the project brief (ask; it is the source of truth).

- [ ] **Step 1:** Write the README section.
- [ ] **Step 2:** `.venv/Scripts/python -m pytest -q` → all pass; report the count.
- [ ] **Step 3: Commit** `docs(social): posting workflow setup and usage`
