# YouTube + Google Business Profile in the Social Posting Workflow — Design

Date: 2026-11-11
Business: Mariachi El Cuis (`el-cuis`)
Status: approved by user 2026-11-12
Builds on: `docs/superpowers/specs/2026-11-10-social-posting-workflow-design.md`

## Intent

`/schedule-social` already queues each new Ready-folder video to Facebook,
Instagram and TikTok in the same slot after one batch approval. This adds two
more destinations for every video in that same batch:

1. **YouTube**: the same video file, same slot.
2. **Google Business Profile (GBP)**: a companion post the same day, same slot.

Why (user's words): the audience is **local LA families booking mariachi for
parties, and they should call us**. Every post on every platform should now
lead to a phone call to **626-922-0091**, not only a website visit.

## Goals

- Every video approved in a `/schedule-social` batch is queued to Facebook,
  Instagram, TikTok, YouTube and GBP, all in the same slot, under the same single
  batch approval.
- **Phone number everywhere it is allowed**: 626-922-0091 appears in the caption
  on Facebook, Instagram, TikTok and in the YouTube description, enforced by
  the posting tools like the other caption rules (hook, https link, hashtags,
  city). GBP is the exception (see GBP below).
- **YouTube title** = song name + a Spanish hook, e.g.
  `Reloj | Mariachi en vivo para tu fiesta en Los Ángeles` (user decision).
  At most 100 characters (YouTube limit).
- **YouTube publishes on its own at the slot.** No tap needed, unlike TikTok.
- **GBP post** = a still frame from the video as the photo, a short Spanish
  hook plus a booking line, and a **Call** button (user decision).
- **Only new videos going forward** (user decision): videos already scheduled
  to Facebook/Instagram/TikTok before this ships are never offered to YouTube
  or GBP. No backfill.
- Never post the same video twice to YouTube or GBP (same guarantees as the
  other platforms: posting log + S3 queue checks).

## Non-goals

- Backfilling older videos onto YouTube or GBP.
- Uploading the video itself to GBP. Google's API does not put a video in a
  GBP post; the post uses a frame.
- YouTube playlists, custom thumbnails, end screens, comments, community posts.
- Changing the posting rhythm, slot rules, caption data sources or the
  Instagram/TikTok/Facebook publishing mechanics.
- Changing the existing GBP queue used for GQ Painting
  (`servers/gbp/scheduled_posts.json` + `scripts/run-scheduled-posts.py`).
  El Cuis GBP posts go through the social workflow instead.
- GBP for RnR or GQ.

## Anti-goals (failure even if it "works")

- A video showing up twice on YouTube, or two GBP posts for one video.
- A flood of old videos posted to YouTube/GBP the first time the new version
  runs.
- A YouTube or GBP post going out that the user did not see in the approval
  table.
- A Facebook/Instagram/TikTok/YouTube caption without the phone number.
- A GBP post rejected by Google for policy (see GBP: no phone number in post
  text) and nobody told.
- A YouTube upload that silently stays private forever.

## Constraints and existing pieces

- Videos: vertical 1080×1920 Reels (and 1080×1350 Post cuts; Reel is
  preferred by the scan). Lengths 18 s to ~3:25. YouTube treats vertical videos
  up to 3:00 as Shorts; longer ones become regular videos. **My call (proposed in
  chat, not objected to): longer videos still post, as regular videos.** Show in the
  approval table which videos will not be Shorts.
- Videos live only on the user's PC. Uploads that need the file at approval time
  run in the local stdio MCP servers (as Facebook does today). Anything that
  publishes later runs in the EC2 runner (`scripts/run-scheduled-social.py`)
  from the S3 media queue (`shared/media_queue.py`), as Instagram and TikTok do.
- Posting log: `shared/social_ledger.py`, `PLATFORMS = ("facebook",
  "instagram", "tiktok")`. Scan: `shared/social_scan.py` computes
  `missingPlatforms` per candidate from `profile["platforms"]`. Profile:
  `social/profiles/el-cuis.json`.
- Caption rules: `shared/caption_rules.py` (`check_caption`). Rules are
  blocking and enforced in the write tools.
- Write tools follow the repo contract: call without `approval_id` = dry run
  with preview + approvalId; call again with the approvalId to execute;
  approvalIds expire after 15 min (CLAUDE.md standing rules 7–8).
- YouTube today: `servers/youtube/` is read-only; `scripts/youtube-auth.py`
  requests `youtube.readonly` + `yt-analytics.readonly`. Config secret
  `/ads-mcp/el-cuis/youtube/config`.
- **YouTube API limits the builder must handle:**
  - Quota: Google's quota page (checked 2026-11-11) gives projects a default
    of 100 `videos.insert` calls per day in their own bucket, so a batch of up
    to 9 posts fits easily. Older docs saying 1,600 units of 10,000 are out
    of date.
  - **No audit needed:** the YouTube API Services team confirmed to the user
    on 2026-11-12 that videos uploaded through the API are *not*
    automatically made private (third-party claims to the contrary are out of
    date). YouTube posting can launch together with GBP.
- GBP today: `servers/gbp/` (`gbp_client.create_post`, `upload_photo`) works
  for GQ; GBP is disabled in `clients/*.json` for RnR and GQ. El Cuis has a
  **verified** Business Profile (user) but no GBP config yet. Config secret
  `/ads-mcp/el-cuis/gbp/config` needs `client_id`, `client_secret`,
  `refresh_token`, `gbp_location_id` (`shared/manifest.py`), plus
  `gbp_account_id`, which `servers/gbp/gbp_client.py` also reads.
- GBP's API has no native scheduled publish, so the GBP post must be created by
  the EC2 runner at the slot.
- GBP content policy rejects phone numbers in the post text. The Call button
  dials the phone number on the listing itself, so the listing's primary
  phone must be 626-922-0091. Check it during setup and tell the user if it is
  not.

## How it works (decided parts)

### 1. Profile

`social/profiles/el-cuis.json` gains:
- `"phone": "626-922-0091"`
- `"platforms": ["facebook", "instagram", "tiktok", "youtube", "gbp"]`
- A start time for each new platform (field name left to the builder). GBP
  and YouTube have separate start times, because YouTube may switch on later
  (see 3).

### 2. Caption rules

- New blocking rule **phone**: the caption contains the profile phone number
  (tolerate common formats: `626-922-0091`, `(626) 922-0091`, `626.922.0091`,
  `+1 626…`). Applies to Facebook, Instagram, TikTok and the YouTube
  description.
- YouTube title rule: non-empty, ≤100 characters, starts with the song name,
  contains no `<` or `>` (YouTube rejects them).
- GBP text rules: has a hook first line, no phone number in the text (GBP
  policy), ≤1,500 characters (GBP limit), Call button present.
- Existing rules (hook, https link, hashtags, city) stay as they are for
  Facebook, Instagram, TikTok and the YouTube description.

### 3. Only new videos (no backfill)

My call: when each new platform switches on, take a **snapshot of every song
key already in the posting log** (and in the pending/processing S3 queue) and
store it with that platform's start time. A song in the snapshot is never
offered to that platform, ever. That holds even if its earlier posts later
fail, are cancelled, or a new re-export of it shows up (`possibleRepeat`). Only
songs first seen after the start time get the new platform. This guards
against the flood anti-goal and needs no "first queued at" field in the log.
Retries still work as today: a YouTube/GBP post queued after the start time
that fails is offered again.

### 4. YouTube post

- Uploaded at approval time from the local machine (file is on the PC),
  resumable upload, `privacyStatus: private` with `publishAt` = the slot, so
  YouTube itself makes it public at the slot.
- Title: song name + Spanish hook (Claude writes the hook; same data-driven
  hook approach as the captions).
- Description: the same bilingual caption used on the other platforms (which
  now includes the phone number), with hashtags at the end. YouTube shows the
  first three hashtags above the title, so put the three `alwaysHashtags`
  first.
- `selfDeclaredMadeForKids: false`; category Music (my call; change if the
  user objects).
- After upload, check status: if YouTube rejected the video (processing
  failure, policy rejection), report it as a platform error and mark the log
  `failed`.
- My call (safety net): after the slot, the EC2 runner (or the next
  `/schedule-social` run) checks that the video actually went public. If not,
  it tells the user and does **not** re-offer the video automatically; a second
  upload could be a duplicate.
- New write tool `youtube_schedule_video` (dry run → approvalId → execute),
  plus `youtube_list_scheduled` and `youtube_cancel_scheduled` (deletes the
  private video before its publish time), matching the other platforms'
  tool sets.
- `scripts/youtube-auth.py` adds the `youtube.upload` scope (plus whatever
  delete/status reads need); the user re-runs it once.

### 5. GBP post

- At approval time, locally: extract a strong still frame from the video
  (ffmpeg is installed on the PC), save as JPEG, put it in S3 next to the queue
  entry.
- Queue a GBP entry in the S3 media queue for the slot. At the slot the EC2
  runner creates a STANDARD local post with:
  - the photo (from S3),
  - text: Spanish hook + a short booking line (Spanish first, short English
    line after), and a pointer to the full video on YouTube with its link when
    that YouTube video is public at GBP publish time (the runner checks);
    otherwise the website link,
  - `callToAction: CALL` (dials the listing's phone).
- Same runner rules as Instagram/TikTok: claim before publish, retry
  retryable errors, unknown outcomes keep the posting log `queued` (fix
  808d7ac).
- Dry run shows the chosen frame (path or inline image) and the post text so
  the user can reject a bad frame.

### 6. Approval table

`/schedule-social` step 8 dry-runs every missing platform, now including
YouTube and GBP. The table per video adds:
- YouTube title, whether it will be a Short or a regular video.
- GBP frame + text.
- Phone rule result per platform.
One batch approval still covers everything. Edits re-dry-run the changed
posts on every platform, as today.

### 7. Setup the user does once

1. Re-run `python scripts/youtube-auth.py` (upload scope).
2. Connect El Cuis GBP: OAuth login as the account that manages the El Cuis
   listing, store `/ads-mcp/el-cuis/gbp/config` with the location id. The
   builder adds or extends an auth script like the YouTube/TikTok ones.
3. Confirm the GBP listing's phone is 626-922-0091.
4. Deploy the runner change to EC2.

## Left to the builder

- Frame selection method (a fixed offset, scene detection, or a few
  candidates for Claude to choose from).
- Name and shape of the per-platform start-time field.
- Whether YouTube tools live in `servers/youtube/` (recommended, next to the
  read tools) and how GBP publishing is shared between the GBP server and the
  social runner.
- Exact Spanish booking line wording for GBP (follow the brand brief
  `servers/content-agent/brands/el-cuis.md`).
