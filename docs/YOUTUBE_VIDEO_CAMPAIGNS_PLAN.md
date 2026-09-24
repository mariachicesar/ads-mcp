# YouTube Video Campaigns Plan

## Goal

Extend `servers/google-ads/` (no new server) to support YouTube/video campaigns (`advertising_channel_type = VIDEO`). Today every read tool in `servers/google-ads/tools/read.py` implicitly assumes Search: no query filters or excludes by channel type, and none select video-specific metrics. There is no video campaign creation tool at all — `tools/write.py` only has `create_rsa` (Responsive Search Ad).

---

## What's broken today for a mixed Search + Video account

Confirmed by reading the current queries:

- `list_campaigns` (`servers/google-ads/tools/read.py:226`) already selects `campaign.advertising_channel_type` and returns it as `channel_type` in each row — so a VIDEO campaign already surfaces in the list with `channel_type: "VIDEO"`. Good foundation, but there's no `channelType` filter parameter to ask for only one kind.
- `get_campaign_performance` (`servers/google-ads/tools/read.py:126`) selects `campaign.id`, `campaign.name`, `campaign.status`, `metrics.impressions`, `metrics.clicks`, `metrics.cost_micros`, `metrics.conversions` — **no channel-type filter, no video metrics**. If a VIDEO campaign exists on an account, this query returns it mixed in with Search rows, using Search-shaped metrics (clicks/CTR) that mean something different for video (a "click" on a video ad is not the same signal as a click on a text ad) and omitting the metrics that actually matter for video: `metrics.video_views`, `metrics.video_view_rate`, `metrics.average_cpv`, `metrics.video_quartile_p25_rate` / `p50` / `p75` / `p100_rate`.
- No other read tool (`get_ad_group_performance`, `get_ad_performance`, `get_device_performance`, etc.) filters by channel type either — all of them would silently blend Search and Video rows into one report the moment a video campaign exists.

## Google Ads API facts that shape this plan

- `advertising_channel_type` lives on the `campaign` resource (`campaign.advertising_channel_type`), enum values include `SEARCH`, `DISPLAY`, `VIDEO`, `PERFORMANCE_MAX`, etc. — already partially used.
- Video-specific metrics (`metrics.video_views`, `metrics.average_cpv`, `metrics.video_view_rate`, `metrics.video_quartile_*_rate`) are only populated (non-zero/non-null) on VIDEO campaigns; they exist as fields on `campaign`/`ad_group`/`ad_group_ad` regardless of channel type, so selecting them on a Search-only account just returns zeros — safe to add without breaking existing Search reporting, but confusing if shown next to Search rows without a channel-type split.
- Video campaigns require a `video_ad` on the ad (referencing an existing uploaded YouTube video by ID), a `TargetCpv` or `TargetCpm` bidding strategy (not `MaximizeClicks`/`MaximizeConversions`, which are Search/Display-oriented — the Google Ads API rejects those strategies on VIDEO campaigns), and a video ad format (`in_stream`, `bumper`, `out_stream`, `video_responsive_ad`, etc.).
- **Critically: a video ad cannot be created "from scratch" through the API.** It references a video already uploaded to a YouTube channel linked to the Google Ads account (via `video.channel_id` / `youtube_video_id` on the ad's `video_ad` object). The Content Agent's job of generating video creative and uploading it to YouTube is out of scope for this plan — this plan assumes a video ID already exists.

---

## Changes to `shared/google_ads_client.py`

Add video-specific snapshot/mutation functions, following the existing pattern (each takes `config` + kwargs, returns a plain dict, raises `AdsMcpError` on failure):

```python
# shared/google_ads_client.py — additions

def get_video_campaign_performance_snapshot(
    config: dict, *, date_range: str, campaign_id: str | None = None, tool: str | None = None,
) -> list[dict]:
    """SELECT campaign.id, campaign.name, campaign.status,
       metrics.impressions, metrics.video_views, metrics.average_cpv,
       metrics.video_view_rate, metrics.cost_micros, metrics.conversions
       FROM campaign
       WHERE campaign.advertising_channel_type = 'VIDEO'
         AND segments.date DURING {date_range}
         AND campaign.status != 'REMOVED'
       [AND campaign.id = {campaign_id}]"""

def create_video_campaign(
    config: dict, *,
    campaign_name: str,
    daily_budget_micros: int,
    bidding_strategy: str,          # "TARGET_CPV" | "TARGET_CPM"
    target_cpv_micros: int | None,
    youtube_video_id: str,
    ad_group_name: str,
    headline: str,
    description: str,
    final_url: str,
    video_ad_format: str,           # "IN_STREAM" | "BUMPER" | "OUT_STREAM" | "VIDEO_RESPONSIVE_AD"
    geo_targets: list[str] | None,  # existing per-business service-area cities, read-only reuse
    tool: str | None = None,
) -> dict:
    """Multi-step mutation, mirroring create_responsive_search_ad's shape but
    with more moving parts: create CampaignBudget -> create Campaign
    (advertising_channel_type=VIDEO, target_cpv or target_cpm) -> create
    AdGroup (type=VIDEO_TRUE_VIEW_IN_STREAM or matching the ad format) ->
    create AdGroupAd with a VideoAd referencing youtube_video_id.
    Each sub-step failure must roll into one AdsMcpError with which step
    failed in `details`, since a partial create (e.g. campaign created but
    ad group failed) leaves an orphaned paused campaign that the dry-run
    preview did not warn about — flag this failure mode explicitly to the
    caller rather than silently leaving partial state."""
```

The multi-step nature of `create_video_campaign` is the main new risk this plan introduces: unlike every existing write tool (`update_campaign_budget`, `set_campaign_status`, `create_rsa`), which is a single mutation against one resource, creating a video campaign requires **budget → campaign → ad group → ad**, four sequential mutations. A failure partway through leaves orphaned Google Ads objects. The dry-run response must say so explicitly (e.g. a `warnings` entry: "Execute performs 4 sequential API calls; a mid-sequence failure will leave a partially created campaign that must be cleaned up manually or via `set_campaign_status` PAUSED/REMOVED.") since there's no cheap transactional rollback across `CampaignBudgetService`, `CampaignService`, `AdGroupService`, and `AdGroupAdService` calls.

---

## New write tool: `create_video_campaign`

Follows the same dry-run/execute shape as `create_rsa` and `update_campaign_budget`:

**Payload:**
```json
{
  "campaignName": "RnR Electrician - YouTube - Brand Awareness",
  "dailyBudget": 10.0,
  "biddingStrategy": "TARGET_CPV",
  "targetCpv": 0.05,
  "youtubeVideoId": "dQw4w9WgXcQ",
  "adGroupName": "In-Stream Ads",
  "headline": "RnR Electrician — Licensed & Local",
  "description": "Fast, reliable electrical service across LA.",
  "finalUrl": "https://www.rnrelectrician.com",
  "videoAdFormat": "IN_STREAM",
  "geoTargets": ["Pasadena", "Glendale"]
}
```

**Dry-run response** (`mode="dry-run"`): since this creates net-new resources, `changes` has no "before" — each of the four sub-resources (budget, campaign, ad group, ad) appears as one `build_change(..., before=None, after=<proposed config>, status="proposed")` entry, so the approver can see exactly what four objects will be created before approving. Rule checks run through `evaluate_google_ads_mutation_rules` with `change_type="campaign-structure"` — for GQ Painting this hits the existing `campaign_structure_requires_explicit_approval` rule and blocks execution without approval, exactly like any other structural change to that account (`shared/rules.py:123`). This is deliberate: creating a new campaign is a structural change under the current rule vocabulary, no new rule category needed.

**Execute**: requires `approvalId`, re-checks rules, then runs the 4-step mutation sequence in `shared/google_ads_client.py`, returning `executed=True` with the created resource names for all four objects (or an `AdsMcpError` naming which step failed, per above).

---

## Do existing tools need a `channelType` parameter?

Yes, for two different reasons depending on the tool:

**`list_campaigns`** — add an optional `channelType` payload field (`"SEARCH" | "VIDEO" | "ALL"`, default `"ALL"`). Low risk: it's additive, defaults to today's behavior, and the query already selects the field, so filtering is a one-line `WHERE` clause addition. Recommended: **add now**, since Video and Search campaigns will otherwise render identically in a single list with no easy way for a caller to separate them once video campaigns exist.

**`get_campaign_performance`** — do **not** silently add video metrics to the existing tool's default output; instead:
- Add the same `channelType` filter parameter, default `"SEARCH"` (preserves today's exact behavior for every existing caller — RnR and GQ have Search-only accounts today, so nothing changes for them).
- When `channelType="VIDEO"`, select the video metric fields instead of click/CTR fields (they mean different things and mixing them in one table is misleading, per the API facts above).
- Do **not** try to build one unified "all channel types, all metrics" table — Search and Video performance are different enough that a caller asking "how did video do" and a caller asking "how did search do" are asking genuinely different questions, and force-merging them produces a report nobody asked for. This is a case where three near-identical query branches (Search default, Video, and eventually Display/PMax if those ever get added) beat one clever unified query.

Every other existing read tool (`get_ad_group_performance`, `get_ad_performance`, `get_device_performance`, `get_geo_performance`, `get_schedule_performance`) stays Search-only for now — extending all of them to Video is unnecessary until a client actually has an active video campaign generating ad-group/ad/device-level data worth reporting on. Extend on demand, not speculatively.

---

## File layout

- `shared/google_ads_client.py` — modify: add `get_video_campaign_performance_snapshot`, `create_video_campaign` (and its internal 4-step helpers).
- `servers/google-ads/tools/read.py` — modify: add `channelType` param to `list_campaigns` and `get_campaign_performance`.
- `servers/google-ads/tools/write.py` — modify: add `create_video_campaign` wrapper (validate payload → rules → snapshot/dry-run → execute), same shape as `create_rsa`.
- `servers/google-ads/main.py` — modify: add `POST /tools/create_video_campaign` route.
- `servers/google-ads/mcp_server.py` — modify: add the matching `@mcp.tool()` entry.
- No changes needed to `shared/manifest.py`, `shared/secrets.py`, or credential shape — video campaigns use the exact same Google Ads OAuth credentials already stored for Search.

---

## Open questions

1. **Which client(s), if any, actually want YouTube video campaigns right now?** Neither RnR's nor GQ's `CLAUDE.md` block mentions video — confirm business intent before building the write side; the read-side channel-type filtering is safe to build regardless since it's non-destructive and improves reporting hygiene even with zero video campaigns.
2. **Is there an existing YouTube channel linked to either Google Ads account?** `create_video_campaign` requires a `youtube_video_id` already uploaded to a channel that's linked in Google Ads' "Linked accounts" settings — if no channel is linked yet, that's a manual one-time setup step in the Google Ads UI before this tool can work at all, independent of any code here.
3. **Who produces the video creative and uploads it to YouTube?** Out of scope for this plan (belongs to the Content Agent or a manual process) — this plan only covers referencing an existing `youtube_video_id`.
4. **Budget source**: does video spend come out of the existing $15/day RnR cap or $500/month GQ cap documented in `CLAUDE.md`, or is it a separate incremental budget? This affects whether `create_video_campaign`'s dry-run should cross-check against the account's total daily spend across all campaigns, which none of today's tools currently do.
5. **Bidding strategy default** — `TARGET_CPV` vs `TARGET_CPM`: confirm which the client wants as the tool's default before locking in the payload schema above, since changing a default after clients start using the tool is a breaking change to dry-run output.

---

## Cross-document build order

Given both plans in this build (Meta Ads, YouTube Video) are independent of each other — none depends on another's code — but share limited implementer/reviewer bandwidth, suggested sequencing:

1. **YouTube Video Campaigns** (this doc) first — smallest surface area, extends an already-battle-tested server (`google-ads`) rather than building a new one, and the `channelType` filter additions to `list_campaigns`/`get_campaign_performance` are safe, additive, low-risk changes worth landing early regardless of whether video write tools are ever used.
2. **Meta Ads Integration** (`docs/META_ADS_INTEGRATION_PLAN.md`) second — larger surface (new client module, ~12 tools, new rules dict) but still extends the same tested `SignedRequestMiddleware` + `ToolRequest`/`build_success_response` scaffolding every other server already uses, so no new architecture, just a new platform.
