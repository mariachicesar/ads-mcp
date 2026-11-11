# Social Posting Workflow — Design

Date: 2026-11-10
Business: Mariachi El Cuis (`el-cuis`)
Status: draft for user approval

## Intent

The user drops finished videos into a folder on their PC, then tells Claude
"schedule new videos". Claude finds the videos that have not been scheduled yet,
writes a data-driven bilingual caption for each, assigns each one to the next
open posting slot, and, after the user approves, queues the same video to
Facebook, Instagram and TikTok at the same time.

The point is that posting is consistent (a fixed weekly rhythm), and every post
is aimed at real demand: which LA-area city, which event type and which landing
page are chosen from the business's own GA4, Search Console, Google Ads and Meta
data, not from guesswork.

## Goals

- One command ("schedule new videos") turns new files in the Ready folder into
  approved, queued posts on all three platforms.
- Every post contains, enforced by the posting tools themselves:
  1. a hook (first line),
  2. at least one `https://mariachielcuis.com/...` link,
  3. hashtags (mariachi context, including currently trending ones),
  4. at least one LA-area service city from the confirmed list.
- Posting rhythm (America/Los_Angeles):
  - Mon–Fri: one post, between 6:00 PM and 9:00 PM.
  - Sat and Sun: two posts, one in the morning and one at 3:00 PM.
  - That is 9 posts per week maximum.
- The same video goes to Facebook, Instagram and TikTok in the same slot.
- Captions are bilingual: Spanish first, then English.
- Never schedule the same video twice.

## Non-goals

- Deciding what videos to shoot. The workflow captions and targets videos that
  already exist.
- Watching the folder automatically. The user adds files and then asks.
- Reposting old videos when the queue runs dry. An empty slot stays empty.
- Fully autonomous posting. Every batch is approved by the user. (Future: the
  same playbook can become the EC2 Content Agent's instructions; out of scope.)
- Paid ads. This is organic posting only.

## Anti-goals (would be failure even if it "works")

- A post going out without a hook, an https link, hashtags, or a city.
- A video that was in the folder before launch getting posted.
- The same song posted twice because it exists as both a `Post` and a `Reel` cut,
  or as a renamed/duplicate copy.
- Captions that claim pricing, availability, guarantees, or service areas not in
  the brand brief (`servers/content-agent/brands/el-cuis.md`).
- Anything queued without the user seeing it first (CLAUDE.md standing rules 7–8).

## Constraints and existing pieces

- Videos live at `C:\Users\cesar\Desktop\Mariachi\Assets\Ready`. The EC2 server
  cannot see this folder; the local stdio MCP servers (`servers/meta-ads/local_stdio.py`,
  `servers/tiktok/local_stdio.py`) can, so file scanning and uploads happen locally.
- Existing write tools (all dry-run → approvalId → execute, approvals expire in 15 min):
  - `meta_ads_schedule_instagram_reel` — uploads to S3 queue; EC2 runner publishes
    up to 30 min after `scheduled_time`.
  - `meta_ads_schedule_page_reel` / `meta_ads_schedule_page_video` — Facebook native
    scheduling; Facebook publishes at `scheduled_time`.
  - `tiktok_schedule_inbox_video` — S3 queue; runner sends the video to the TikTok
    inbox as a draft; user taps Post. **The caption cannot be attached on TikTok**;
    it is saved and the user pastes it. Same for any location tag.
- S3 videos are stored under their SHA-256 (`shared/media_queue.put_video`), and an
  existing object is not re-uploaded, so Instagram and TikTok share one upload.
- The EC2 runner checks the queue at :00 and :30, so slot times are on :00 or :30.
- Files are large (65–520 MB today). Future videos are expected to be 30–60 s.
- Brand facts (website, phone, service cities, voice, guardrails) come from
  `servers/content-agent/brands/el-cuis.md`.
- Confirmed service cities: Beverly Hills, Whittier, Downey, Pasadena, La Habra,
  Altadena, West Hollywood, Hollywood, Santa Monica, Compton.

## How it works (decided parts)

### 1. Trigger

The user runs it by asking ("schedule new videos"). It is a Claude Code **skill**
(a playbook Claude follows), not a background agent. User's decision.

### 2. Finding new videos

- "Start fresh" (user's decision): files whose creation time is **before
  2026-11-10 7:00 PM America/Los_Angeles** are baseline — never posted. The
  first run records them in the log as `baseline`. Files created after the cutoff
  are candidates, even if added before the first run. (Cutoff by date is Claude's
  call, so a video added before the first run is not silently skipped.)
- The log also tracks a **normalized song name** (filename minus `Reel`/`Post`,
  copy suffixes like `(1)`, digits, spacing and case). A new file whose song
  matches a done or baseline song is not auto-scheduled; it is shown in the batch
  review flagged "possible repeat" for the user to include or skip. (Claude's call.)
- A **posting log** records each video by SHA-256 fingerprint, with which
  platforms it was queued to and when. It lives in S3 next to the queue so it is
  not tied to one PC. Renames and exact duplicate copies are caught by the hash.
- Candidates are ordered oldest first by file creation date.
- **Post/Reel pairs**: files named `<Song> Reel.mp4` and `<Song> Post.mp4` are two
  cuts of one song and count as one video. The Reel cut (vertical) is used for all
  three platforms; if only a Post cut exists, that one is used. Scheduling one cut
  marks the song as done. (Claude's call.)

### 3. Slots

- Weekday slot: one time between 6:00 and 9:00 PM on :00/:30. Default 7:00 PM.
  When organic Meta data shows a clearly better hour for this audience, use it.
  (Claude's call; see builder notes.)
- Weekend slots: morning = 10:00 AM (Claude's guess, user did not object), and
  3:00 PM.
- New videos fill the next open slots in order, starting at the first slot at
  least ~1 hour in the future. A slot is "open" if no pending/scheduled post for
  `el-cuis` exists in it on any platform.
- Each run fills **at most the next 7 days** (up to 9 slots). Extra videos wait
  for the next run, so trending tags and demand data stay fresh. (Claude's call.)

### 4. Caption

Format (bilingual, Spanish first):

```
<Hook in Spanish>
<1–2 lines Spanish>

<Hook in English>
<1–2 lines English>

📍 <City> · <City>
🔗 https://mariachielcuis.com/<page>

#hashtag #hashtag ...
```

- **Hook**: the first line; short, scroll-stopping, built from the song and the
  demand signal (e.g. "¿Quinceañera en Downey? Así suena 'Hermoso Cariño' en vivo 🎺").
- **Link**: a real page on mariachielcuis.com, chosen from data (GA4 landing pages
  that convert, Search Console pages ranking for the chosen city/event). On
  Instagram and TikTok the link is not clickable; text still includes the URL.
- **Hashtags**: 8–12 in one mixed Spanish/English block. Always-on tags
  (`#mariachi #mariachiloscangeles #mariachielcuis`), song/event tags, city tag,
  and current trending mariachi / LA-event tags found by a web search at batch
  time (none of the connected sources reports trending tags).
- **City**: 1–2 cities from the confirmed list, chosen from where Search Console
  queries and Google Ads search terms show demand, rotated so the same city is not
  used in two consecutive posts.
- Voice and guardrails from the brand brief.

### 5. Data used

| Source | Used for |
|---|---|
| Search Console | queries/pages by city and event type → city, topic, link |
| Google Ads search terms | same, paid side |
| GA4 | which landing pages turn social traffic into inquiries → link |
| Meta organic insights | which past hooks/lengths/times got reach & saves; best hour |
| Web search | trending mariachi/event hashtags this week |

**New read tool (in scope, user's decision):** the current Meta read tools only
return **ad** insights. This project adds a read-only organic insights tool to the
meta-ads server covering:

- Instagram: recent posts/Reels with reach, plays/views, likes, comments, saves,
  shares, caption and publish time; and audience activity by hour if available
  (e.g. `online_followers`).
- Facebook Page: recent posts/Reels with reach, engagement and publish time.

The skill uses it to pick the weekday hour inside 6–9 PM and to learn which hooks,
cities and topics performed. With too little data (new account history, API
metric missing), fall back to 7:00 PM and skip that signal. TikTok organic
insights are out of scope (the inbox-draft integration has no analytics scope).

### 6. Rule enforcement in the posting tools

A shared check (e.g. in `shared/rules.py`) runs in the dry run and the execute of
`meta_ads_schedule_instagram_reel`, `meta_ads_schedule_page_reel`,
`meta_ads_schedule_page_video`, and `tiktok_schedule_inbox_video` when the business
has social caption rules configured (El Cuis does). The post is rejected unless:

1. the first line is a non-empty hook,
2. it contains at least one `https://` URL on the business's website domain
   (`mariachielcuis.com`); `http://` is rejected,
3. it contains at least one hashtag (and stays within platform limits, e.g.
   Instagram 30),
4. it names at least one city from the business's service-city list.

These rules live in business config, not hard-coded to El Cuis, so other
businesses can opt in.

### 7. Approval

User's decision (option 1a), chosen because approvals expire after 15 minutes
and a 27-post batch with large uploads would outlive them:

- Claude runs the dry run for every post in the batch and shows them all at
  once: per video — slot, file, the full caption, link, cities, hashtags, and each
  platform's rule-check results.
- The user approves the batch once. Nothing is queued before that.
- Then, post by post, Claude runs a **fresh dry run** and executes it immediately
  with that fresh approvalId. If the fresh dry run's arguments or rule checks
  differ from what the user saw, or a rule fails, Claude stops and asks before
  continuing.
- The approval store, its 15-minute TTL, and the four tools' dry-run/execute
  contract are **unchanged**; this lives entirely in the skill.
- After execution, the posting log is updated, and Claude reports what was queued.
  TikTok captions are listed for the user to paste when they tap Post.

### 7b. Partial failure

User's decision (option 2a): if one platform fails for a video (rule check, size
limit, API error) while others succeed, keep the successful ones, mark the song
done **for those platforms** in the log, and report the failure. On the next run,
the failed platform is offered again in the next open slot. The log therefore
tracks state per platform per song, and a slot counts as filled if any platform
has a post in it.

### 8. Location tag

The city always appears in the caption text. Instagram and Facebook also get a
real location tag if their API allows it for this post type; TikTok inbox drafts
cannot carry one. (Claude's call.)

## Left to the builder

- Posting log format and S3 key layout; how cancellation of a queued post updates it.
- Caching hashes locally (path + size + mtime) so the 11 GB baseline isn't
  re-hashed every run.
- Exact Meta organic insights endpoints and the threshold for "clearly better hour".
- Whether Facebook gets a Reel or a Page video post (Reel preferred for the
  vertical cut).
- Where the skill file lives (`.claude/skills/...` in this repo is the natural spot).
- Whether location tags are feasible per platform; skip if not.
- Hook length limit and how "first line is a hook" is checked (non-empty, under
  ~150 characters is enough).
