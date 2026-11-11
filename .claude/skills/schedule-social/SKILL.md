---
name: schedule-social
description: Use when the user says "schedule new videos", "schedule social posts", or asks to queue El Cuis (Mariachi El Cuis, business_key el-cuis) videos to Facebook, Instagram and TikTok. Finds new videos in the Ready folder, writes data-driven bilingual captions, assigns posting slots, and queues posts after one batch approval.
---

# Schedule social posts (El Cuis)

Business key: `el-cuis`. Same video goes to Facebook, Instagram and TikTok in the same slot. Organic posting only.

## Hard rules

- Nothing is queued before the user approves the batch (CLAUDE.md rules 7-8). Dry runs only until then.
- Never post a baseline file. Never schedule a song twice.
- Never edit a scheduled post without a new dry run and approval.
- Never change campaign settings (Google Ads or Meta). Data tools are read-only here.
- No pricing, availability, guarantees, awards, or service areas outside the brand brief.
- If a data tool is unavailable or errors, continue with what you have and say so in the batch preview. Never block on it.

## Checklist

### 1. Load context
Read `servers/content-agent/brands/el-cuis.md` and `social/profiles/el-cuis.json` (service cities, `alwaysHashtags`, `hookMaxChars`, slots).

### 2. Find new videos
Call `social_find_new_videos(business_key="el-cuis")` (server `social`).
- No `candidates`: tell the user there is nothing new and STOP. Do not pull any data.
- List `skipped` items and why.
- `instagramTooLarge` candidate: ask for a re-export under 300 MB. Until the user answers, Instagram is excluded for that file (Facebook + TikTok only).
- `possibleRepeat` candidate: ask the user to include or skip. Until the user answers, it is excluded (skipped).
- Collect the answers to these questions before step 8; dry-run only what the user included.
- A candidate's `missingPlatforms` lists the platforms still to schedule (a failed platform from an earlier run is re-offered). Only dry-run those.

### 3. Best weekday hour
Call `meta_ads_get_organic_insights(business_key="el-cuis")`. Use `recommendedWeekdayTime` ("HH:00", inside 18:00-21:00) for weekdays; note `recommendationReason`. Also note which hooks, topics and cities got reach and saves. If missing or errored, use the default (19:00) and say so.

### 4. Slots
Call `social_next_open_slots(business_key="el-cuis", weekday_time=<recommendedWeekdayTime>)`. Assign candidates oldest-first to slots in order. Extra candidates wait for the next run (say so). Fewer candidates than slots is fine; leave slots empty.

### 5. Demand data (last ~4 weeks)
Pull, each independently skippable:
- `search_console_get_search_performance(business_key="el-cuis", date_range="LAST_28_DAYS", dimension="query", limit=50)` and again with `dimension="page"`.
- `google_ads_get_search_terms_report(business_key="el-cuis", date_range="LAST_30_DAYS")`.
- `analytics_get_top_pages(business_key="el-cuis", date_range="LAST_28_DAYS", limit=20)`.

Per video choose:
- Event topic (quinceanera, wedding, birthday, etc.) from demand, matched to the song where sensible.
- 1-2 cities from the confirmed list only: Beverly Hills, Whittier, Downey, Pasadena, La Habra, Altadena, West Hollywood, Hollywood, Santa Monica, Compton. No city in two consecutive posts of the batch.
- A real `https://mariachielcuis.com/...` page that appears in GA4 or Search Console page data. If no page data was available, use only the homepage `https://mariachielcuis.com/` and flag it in the preview.

### 6. Trending tags
Ask the user once (skippable): "Paste any trending searches or hashtags from TikTok Creator Search Insights (in-app only, no API)." Then web search for current mariachi / LA event hashtags this week. Pasted terms rank first.
Per post pick 8-12 hashtags in one mixed Spanish/English block: the `alwaysHashtags`, song/event tags, a city tag, then trending tags. Instagram max is 30; stay well under.

### 7. Write captions
Spanish first, then English. Use this format exactly:

```
<Hook in Spanish>
<1-2 lines Spanish>

<Hook in English>
<1-2 lines English>

📍 <City> · <City>
🔗 https://mariachielcuis.com/<page>

#hashtag #hashtag ...
```

- Line 1 is the hook: short, scroll-stopping, under `hookMaxChars` (150).
- Must contain: a hook, an `https://` mariachielcuis.com link (never `http://`), hashtags, and at least one confirmed city. The posting tools enforce this.
- Voice: warm, celebratory, professional. Follow the brand guardrails above. Treat copy as a draft for the user.
- Same caption text on all three platforms.

### 8. Dry-run every post on every missing platform
No `approval_id`. Use the file path from the candidate and the slot `pacific` time as `scheduled_time`.
- Facebook: `meta_ads_schedule_page_reel(business_key, file_path, scheduled_time, description=<caption>)`
- Instagram: `meta_ads_schedule_instagram_reel(business_key, file_path, scheduled_time, caption)`
- TikTok: `tiktok_schedule_inbox_video(business_key, file_path, scheduled_time, caption)`

Record each `approvalId` and the rule checks. If any dry run is blocked or a rule check fails, fix the caption and re-run the dry run, or show the problem and ask.

### 9. Batch preview
Show one table per video: slot (Pacific), file, platforms, rule checks (hook, https link, hashtags, city) per platform, then the full caption underneath. Add notes: skipped files, repeats, size problems, data tools that were unavailable, and that TikTok drafts arrive in the inbox up to 30 minutes after the slot. Ask "Approve this batch?" and WAIT for one explicit batch approval. Approvals expire after 15 minutes, so do not execute from the old approvalIds.

If the user changes a caption, time or file after seeing the preview, re-dry-run the changed posts on every platform and show them again for approval. Dropping a post needs no re-approval.

### 10. Execute, post by post
For each post, in slot order, per platform:
1. Fresh dry run with arguments identical to those shown in the preview.
2. If the arguments or rule checks differ from what the user saw, or anything is blocked: STOP and ask before continuing.
3. Otherwise execute immediately: same call, same exact arguments, plus `approval_id` from that fresh dry run.

### 11. Handle results
- A platform error: keep the other platforms, report the error. The log marks that platform `failed`, and the next run offers it again.
- A result carrying `ledgerWarning`: the post went out but the log write failed. Tell the user, and warn that the next run may re-offer that platform: do not approve it twice.

### 12. Finish
Report:
- What was queued per platform (video, slot, platform).
- Failures and warnings.
- Every TikTok caption in full, for pasting (TikTok drafts carry no caption).
- Reminder: TikTok drafts arrive in the TikTok inbox up to 30 minutes after the slot; the user taps Post. Instagram publishes up to 30 minutes after the slot.
- Videos left for the next run.
