# ads-mcp

Separate MCP service workspace for tenant ads, analytics, search-console, and content operations.

## Why this repo exists

This repo is intentionally separate from the Tech Bridge admin dashboard frontend and backend.

- `admin-dashboard-rc` owns the UI for tenant staff and internal admins.
- `backend-rc` can own approval records, audit trails, and tenant mappings.
- `ads-mcp` owns external marketing platform integrations, AWS deployment, and MCP server execution.

That separation keeps ad-platform credentials, long-running services, and write-capable automation outside the web app runtime.

## Initial scope

Phase 1 scaffolds:

- Google Ads MCP server
- Meta Ads MCP server
- GA4 MCP server
- Search Console MCP server
- Content Agent MCP server
- Google Business Profile MCP server
- Cross-agent Orchestrator MCP server
- AWS EC2 + Nginx + systemd deployment assets
- OAuth/bootstrap/helper scripts
- Integration contract docs for the existing dashboard/backend

## Operating rules

- All write operations are dry-run first.
- All write operations require explicit user confirmation before execution.
- Business-specific rules belong in `CLAUDE.md` and must be enforced by tools before mutating anything.
- Credentials must be loaded from AWS Secrets Manager, never from committed `.env` files.

## Local dev setup

**1. Create and activate a virtual environment**

```bash
# Git Bash / macOS / Linux
python -m venv .venv
source .venv/bin/activate
```
```powershell
# PowerShell (Windows)
python -m venv .venv
.venv\Scripts\activate
```

**2. Install all dependencies**

```bash
pip install fastapi uvicorn boto3 google-ads pydantic redis fastmcp \
  facebook-business google-analytics-data google-api-python-client \
  google-auth anthropic
```

**3. Create your local credentials file**

Copy the structure below into `local-dev-config.json` (already gitignored) and fill in real values:

```json
{
  "rnr-electrician": {
    "customer_account_id": "...",
    "manager_account_id": "...",
    "developer_token": "...",
    "client_id": "...",
    "client_secret": "...",
    "refresh_token": "...",
    "ga4_property_id": "...",
    "site_url": "https://www.rnrelectrician.com"
  },
  "gq-painting": { "..." : "..." }
}
```

**4. Export the config as an environment variable**

```bash
# Git Bash / macOS / Linux only — PowerShell users skip this; dev-run.py sets env vars automatically
export ADS_MCP_GOOGLE_ADS_CONFIGS_JSON=$(cat local-dev-config.json)
export ADS_MCP_REQUIRE_SIGNED_REQUESTS=false
```

**5. Run all servers**

```bash
python scripts/dev-run.py
```

All seven servers start on ports 8001-8007 with signed-request validation disabled. Ctrl+C stops them all.

---

## Connecting Claude Desktop

Each server has a `mcp_server.py` that speaks the MCP stdio protocol. Add them to your Claude Desktop config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "google-ads": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/ads-mcp/servers/google-ads/mcp_server.py"],
      "env": {
        "ADS_MCP_GOOGLE_ADS_CONFIGS_JSON": "<paste contents of local-dev-config.json here>",
        "ADS_MCP_REQUIRE_SIGNED_REQUESTS": "false"
      }
    },
    "analytics": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/ads-mcp/servers/analytics/mcp_server.py"],
      "env": {
        "ADS_MCP_ANALYTICS_CONFIGS_JSON": "<paste contents of local-dev-config.json here>",
        "ADS_MCP_REQUIRE_SIGNED_REQUESTS": "false"
      }
    },
    "search-console": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/ads-mcp/servers/search-console/mcp_server.py"],
      "env": {
        "ADS_MCP_SEARCH_CONSOLE_CONFIGS_JSON": "<paste contents of local-dev-config.json here>",
        "ADS_MCP_REQUIRE_SIGNED_REQUESTS": "false"
      }
    },
    "content-agent": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/ads-mcp/servers/content-agent/mcp_server.py"],
      "env": {
        "ADS_MCP_GOOGLE_ADS_CONFIGS_JSON": "<paste contents of local-dev-config.json here>",
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "ADS_MCP_REQUIRE_SIGNED_REQUESTS": "false"
      }
    },
    "gbp": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/ads-mcp/servers/gbp/mcp_server.py"],
      "env": {
        "ADS_MCP_GBP_CONFIGS_JSON": "<paste contents of local-dev-config.json here>",
        "ADS_MCP_REQUIRE_SIGNED_REQUESTS": "false"
      }
    },
    "orchestrator": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/ads-mcp/servers/orchestrator/mcp_server.py"],
      "env": {
        "ADS_MCP_REQUIRE_SIGNED_REQUESTS": "false"
      }
    }
  }
}
```

On **Windows**, use the `.venv\Scripts\python.exe` path and backslashes in paths, or use forward slashes — both work in JSON. Restart Claude Desktop after editing the config.

---

## Deploying to EC2

### Prerequisites
- EC2 instance (t3.small minimum) running Ubuntu 24.04
- IAM role attached with `secretsmanager:GetSecretValue` permission
- Your EC2 public IP in `.env` as `EC2_PUBLIC_IP`

### 1. First-time bootstrap

```bash
# Git Bash
bash infrastructure/setup-ec2.sh
```

### 2. Push credentials to AWS Secrets Manager

Fill in `local-dev-config.json` with all credentials, then:

```powershell
# PowerShell or Git Bash
python scripts/push-secrets.py --dry-run

python scripts/push-secrets.py
```

Secrets are written to `/ads-mcp/{business-key}/{platform}/config` for each business + platform combination.

> **No local AWS credentials?** The EC2 instance has the `rd-mcp-ec2-role` IAM role attached, so you can run the push from the instance instead:
> ```powershell
> # PowerShell or Git Bash — scp/ssh work in both on Windows
> # EC2_PUBLIC_IP is set in your .env file
> scp -i ~/.ssh/rd-mcp-key.pem local-dev-config.json ubuntu@$EC2_PUBLIC_IP:/opt/ads-mcp/local-dev-config.json
> ssh -i ~/.ssh/rd-mcp-key.pem ubuntu@$EC2_PUBLIC_IP "cd /opt/ads-mcp && .venv/bin/python scripts/push-secrets.py"
> ssh -i ~/.ssh/rd-mcp-key.pem ubuntu@$EC2_PUBLIC_IP "rm /opt/ads-mcp/local-dev-config.json"
> ```

### 3. Deploy latest code

```bash
# Git Bash
bash scripts/deploy.sh
```

This streams a tar archive of the repo to `/opt/ads-mcp` on the instance, installs dependencies, updates systemd unit files, restarts all services, and verifies HTTPS health endpoints.

By default, deploy does **not** overwrite the live nginx site file so certbot-managed HTTPS config is preserved.

Use this only when you intentionally want to apply the repo nginx template:

```bash
UPDATE_NGINX=true bash scripts/deploy.sh
```

### 4. Enable HTTPS (first time only)

```bash
bash infrastructure/enable-https.sh
```

Requires a domain pointing at your EC2 IP. Configures Nginx + Let's Encrypt.

### 5. Check service health

```bash
# Git Bash
bash scripts/health-check.sh
```

Each service exposes a `/health` endpoint:

| Service | Port |
|---|---|
| google-ads | 8001 |
| meta-ads | 8002 |
| analytics | 8003 |
| search-console | 8004 |
| content-agent | 8005 |
| gbp | 8006 |
| orchestrator | 8007 |

### Remote HTTPS snippets (backend-rc + Claude Desktop)

Use these snippets to standardize on the deployed remote base URL:

### Developer onboarding: add MCP by URL

Use this when a developer's MCP client supports URL-based remote servers.

1. Add the remote server config in the client config file:

```json
{
  "mcpServers": {
    "ads-mcp-remote": {
      "url": "https://mcp.rdtechbridge.com/orchestrator/",
      "headers": {
        "X-Environment": "production"
      }
    }
  }
}
```

2. Restart the MCP client.

3. Validate connectivity with health checks:

```bash
curl -sS https://mcp.rdtechbridge.com/health
curl -sS https://mcp.rdtechbridge.com/orchestrator/health
```

4. Run dry-run tools first, then route execute/write operations through backend-rc for approvals and signatures.

If a client does not support URL-based MCP servers yet, use local stdio server config (`command` + `args`) and keep backend traffic pointed at `https://mcp.rdtechbridge.com`.

```bash
# backend-rc environment
ADS_MCP_BASE_URL=https://mcp.rdtechbridge.com
ADS_MCP_TIMEOUT_SECONDS=30
```

```json
{
  "mcpRequest": {
    "tenantId": 12,
    "businessKey": "rnr-electrician",
    "requestedBy": 44,
    "dryRun": true,
    "payload": {
      "campaignName": "Main Search",
      "newDailyBudget": 20
    },
    "requestMeta": {
      "actionRequestId": "mar_123",
      "source": "backend-rc"
    }
  }
}
```

```bash
# backend-rc -> ads-mcp dry-run example
curl -X POST "https://mcp.rdtechbridge.com/google-ads/tools/update_campaign_budget" \
  -H "Content-Type: application/json" \
  -H "X-AdsMcp-Signature: <backend-generated-signature>" \
  -d '{
    "tenantId": 12,
    "businessKey": "rnr-electrician",
    "requestedBy": 44,
    "dryRun": true,
    "payload": {
      "campaignName": "Main Search",
      "newDailyBudget": 20
    }
  }'
```

### HMAC Request Signing

All production requests from `backend-rc` to these servers are signed with HMAC-SHA256. Each server verifies the signature before processing the request.

**On the ads-mcp EC2 (`/opt/ads-mcp/.env`):**

```env
ADS_MCP_REQUIRE_SIGNED_REQUESTS=true
ADS_MCP_SIGNING_KEYS_JSON={"backend-rc":"<32-byte hex secret>"}
```

Generate a new secret:
```bash
openssl rand -hex 32
```

**On the backend EC2 (`backend-rc/.env`):**

```env
ADS_MCP_KEY_ID=backend-rc
ADS_MCP_SIGNING_SECRET=<same 32-byte hex secret>
```

The key ID (`backend-rc`) must appear as a key in `ADS_MCP_SIGNING_KEYS_JSON`. Multiple callers can each have their own entry.

> **systemd warning:** Do NOT put `ADS_MCP_SIGNING_KEYS_JSON` in a systemd `Environment=` directive — systemd strips curly braces from inline values. Always use `EnvironmentFile=/opt/ads-mcp/.env` in all unit files and keep the JSON value there.

After updating `/opt/ads-mcp/.env`, reload and restart all services:

```bash
sudo systemctl daemon-reload
sudo systemctl restart google-ads-mcp meta-ads-mcp analytics-mcp search-console-mcp content-agent-mcp gbp-mcp orchestrator-mcp
```

---

### AWS Security Group

The ads-mcp EC2 must allow inbound TCP on ports 8001–8007 from the backend EC2's VPC subnet so `backend-rc` can reach the services over the private network.

Required inbound rule on the ads-mcp security group:

| Type | Protocol | Port range | Source |
| ---- | -------- | ---------- | ------ |
| Custom TCP | TCP | 8001–8007 | `172.31.0.0/16` (VPC subnet CIDR) |

Without this rule, all requests from backend-rc will time out regardless of signing config.

---

### Scheduled GBP posts (cron)

Posts queued via `gbp_create_post` with a `scheduled_time` live in `servers/gbp/scheduled_posts.json`. Add this cron on the EC2 instance to publish them:

```bash
# Run every 15 minutes so posts fire close to their scheduled time
*/15 * * * * /opt/ads-mcp/.venv/bin/python /opt/ads-mcp/scripts/run-scheduled-posts.py >> /var/log/ads-mcp/scheduled-posts.log 2>&1
```

---

### Meta video publishing (Facebook Page + ad library)

The meta-ads server can upload ad videos and schedule Facebook Page videos and Reels. Facebook holds scheduled posts itself, so there is no queue or cron for these (Instagram scheduling is not supported yet).

| Tool | What it does |
|------|--------------|
| `meta_ads_upload_ad_video` | Uploads a video to the ad account's Media library (`advideos`) and returns its video ID |
| `meta_ads_schedule_page_video` | Schedules a Page video post (`published=false` + `scheduled_publish_time`) |
| `meta_ads_schedule_page_reel` | Schedules a Page Reel (start → upload → finish with `video_state=SCHEDULED`) |
| `meta_ads_list_scheduled_page_videos` | Lists scheduled, not-yet-published Page videos/Reels |

- Source: exactly one of `file_path` (`.mp4`/`.mov`, max 1 GiB, readable by the server process) or `file_url` (public `https://`).
- Times: ISO 8601; a time without an offset is Pacific (`America/Los_Angeles`). Must be 10 minutes to 75 days out, otherwise the dry run is blocked.
- Only the configured `page_id` can be posted to.
- Write tools use the usual dry run → `approvalId` flow. For local files the approval is bound to the file's SHA-256, so editing the file after the dry run invalidates the approval.
- The Meta user token needs `pages_manage_posts`, `pages_read_engagement` and `pages_show_list` (plus the ads permissions). The Page token is fetched per call and never stored. Cancel or edit scheduled posts in Meta Business Suite.

### Instagram scheduled Reels (S3 queue + 30-minute timer)

Instagram's API cannot schedule posts, so the meta-ads server queues them in S3 and `scripts/run-scheduled-social.py` publishes due posts from the `ads-mcp-social.timer` systemd timer **every 30 minutes** (a post goes out up to 30 minutes after its scheduled time).

| Tool | What it does |
|------|--------------|
| `meta_ads_schedule_instagram_reel` | Dry run → approvalId; execute uploads the video to S3 and queues the post |
| `meta_ads_list_scheduled_social` | Lists queued posts (default states: pending, processing, failed) |
| `meta_ads_cancel_scheduled_social` | Cancels a **pending** post (dry run → approvalId) |

- Bucket: `ADS_MCP_MEDIA_BUCKET` (set in `meta-ads-mcp.service`, `ads-mcp-social.service`, and `.env` for local stdio). Configure it once with `bash infrastructure/configure-media-bucket.sh` (Block Public Access, lifecycle, EC2 role access). Instagram fetches the video through a 1-hour presigned URL.
- Source: local `.mp4`/`.mov` file (≤ 1 GiB). Caption required, ≤ 2,200 characters and 30 hashtags. Times as for Facebook scheduling (Pacific default, 10 minutes–75 days out).
- Runner rules: retryable Meta errors retry up to 3 times; posts more than 24 h late fail as missed; an entry stuck in `processing` for over 1 h fails and is **never reposted automatically** — check Instagram, then reschedule if needed.
- Token needs `instagram_basic` and `instagram_content_publish`; the Instagram account must be a professional account linked to the Page (`instagram_user_id` in the secret).
- Logs: `/var/log/ads-mcp/scheduled-social.log`. Timer status: `systemctl list-timers ads-mcp-social.timer`.

### TikTok scheduled inbox drafts (same S3 queue + timer)

The `tiktok` MCP server queues videos in the same S3 queue (`platform: "tiktok"`); the same 30-minute runner sends each due video to the **TikTok app inbox as a draft**. You get a TikTok notification, open the draft, paste the caption and tap **Post**. Direct posting needs TikTok to audit the app; until then, inbox mode is the only option.

| Tool | What it does |
|------|--------------|
| `tiktok_schedule_inbox_video` | Dry run → approvalId; execute uploads the video to S3 and queues it |
| `tiktok_list_scheduled` | Lists queued TikToks with Pacific time and the **saved caption to paste** |
| `tiktok_cancel_scheduled` | Cancels a **pending** TikTok (dry run → approvalId) |

**One-time setup**

1. On developers.tiktok.com, create an app with **Login Kit** and **Content Posting API**, scopes `user.info.basic` and `video.upload`, and redirect URI `https://mariachielcuis.com/`.
2. Run `python scripts/tiktok-auth.py` (AWS credentials that can write Secrets Manager). Enter the Client key and secret, open the printed link while logged in to the business TikTok account, tap Authorize, and paste the address TikTok sends you to. This saves `/ads-mcp/el-cuis/tiktok/config`.
3. Give the EC2 role `secretsmanager:PutSecretValue` on `arn:aws:secretsmanager:*:*:secret:/ads-mcp/*/tiktok/config*`. TikTok rotates the refresh token on use and the runner must save the new one; without this permission every TikTok run is requeued and then fails.
4. Register the server for Claude Code: `claude mcp add tiktok -- .venv/Scripts/python.exe servers/tiktok/local_stdio.py` (needs `ADS_MCP_MEDIA_BUCKET` in `.env`).

**Behavior**

- Drafts carry **no caption**; the caption is kept on the queue entry and shown by `tiktok_list_scheduled` and in the entry's result note.
- At most 5 pending TikTok drafts within any 24 h window (rule `tiktok-daily-limit`), matching TikTok's per-user cap.
- Source: local `.mp4`/`.mov` ≤ 1 GiB; times as for Instagram (Pacific default, 10 minutes–75 days out); sent up to 30 minutes after the scheduled time.
- The runner streams the video from S3 in memory (one chunk ≤ 64 MiB, otherwise 10 MiB chunks), never to disk. Upload errors retry up to 3 times; once all chunks are uploaded nothing is retried automatically (check the TikTok inbox before rescheduling).
- The refresh token lasts about a year; if it expires or is revoked, entries fail with a hint to rerun `scripts/tiktok-auth.py`.

### TikTok stats (read-only)

| Tool | What it does |
|------|--------------|
| `tiktok_get_account_stats` | Followers, following, total likes, video count |
| `tiktok_get_video_stats` | Newest videos (`limit` 1-100, optional `days`) with views, likes, comments, shares, engagement rate and totals |

- Needs the **Display API** scopes `user.info.stats` and `video.list` on the TikTok app (add the products/scopes on developers.tiktok.com), then rerun `python scripts/tiktok-auth.py` so the stored token carries them. The script warns if TikTok did not grant them.
- These tools call TikTok directly and refresh the token on each call; a rotated refresh token is saved to Secrets Manager before use (your local AWS credentials need `secretsmanager:PutSecretValue` on the TikTok secret).
- TikTok only returns lifetime counts per video: no per-day views or traffic sources.

### YouTube (read-only, `servers/youtube/`)

| Tool | What it does |
|------|--------------|
| `youtube_get_channel_overview` | Subscribers, lifetime views, video count |
| `youtube_get_recent_videos` | Latest uploads (`limit` 1-50) with views, likes, comments, engagement rate |
| `youtube_get_analytics` | Views, watch minutes, avg view duration, likes, comments, shares, subscribers gained for `LAST_7_DAYS`/`LAST_28_DAYS`/`LAST_30_DAYS`/`LAST_90_DAYS`, by `day`, `traffic_source`, `video`, `country` or `device` (data lags ~2 days) |

**One-time setup**

1. In the Google Cloud project used for the other Google servers, enable **YouTube Data API v3** and **YouTube Analytics API**, and add the scopes `youtube.readonly` and `yt-analytics.readonly` to the OAuth consent screen.
2. `pip install google-auth-oauthlib`, then `python scripts/youtube-auth.py --business-key el-cuis` (same `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`). Sign in with the Google account that owns the channel. This saves `/ads-mcp/el-cuis/youtube/config` (add `--channel-id UC...` if the account manages several channels).
3. Register it: `claude mcp add youtube -s local -- .venv/Scripts/python.exe servers/youtube/mcp_server.py`, or add a `youtube` entry to `claude_desktop_config.json` like `search-console`, then restart.

## Social posting workflow

Schedules the same video to Facebook, Instagram and TikTok in one slot, with a batch approval. Currently configured for El Cuis (`el-cuis`).

**Profile file: `social/profiles/<business>.json`**

| Key | Meaning |
|-----|---------|
| `businessKey`, `websiteDomain` | Business and the domain captions must link to (`https://` only) |
| `serviceCities` | Cities a caption may mention (at least one required) |
| `readyFolder` | Local folder scanned for new `.mp4`/`.mov` videos |
| `baselineCutoff` | Files created before this are baseline (already posted) and never scheduled |
| `hookMaxChars` | Max length of the caption's first line (default 150) |
| `alwaysHashtags` | Hashtags every caption should carry |
| `slots` | `weekdayWindow`, `weekdayDefault`, `weekend` window (Pacific), `horizonDays` to plan ahead, `minLeadMinutes` before a slot can be used |
| `platforms` | Platforms each video goes to |

Override the directory with `ADS_MCP_SOCIAL_PROFILES_DIR` (tests do).

**Caption rules.** When a profile exists for the request's `businessKey`, the Facebook, Instagram and TikTok schedule tools add blocking rule checks: `caption-hook` (first line is text, within `hookMaxChars`), `caption-https-link` (an `https://` link to `websiteDomain`, no `http://`), `caption-hashtags` (at least one), `caption-city` (mentions a service city). Businesses without a profile are unaffected.

**Posting log.** On execute, the schedule tools record each post in S3 at `ledger/<business>.json`, keyed by song, with per-platform state (`queued`, `failed`, `cancelled`), scheduled time and file SHA-256. Cancelling marks the entry cancelled, and the runner marks an Instagram/TikTok entry that permanently fails as `failed` so the next run offers it again. Log write failures never fail the schedule call.

**`social` MCP server** (`servers/social/`, read-only):

| Tool | What it does |
|------|--------------|
| `social_find_new_videos` | New videos in the Ready folder, excluding baseline files and songs already in the log |
| `social_next_open_slots` | Open posting slots from the profile, skipping those already taken |
| `social_get_posting_log` | The S3 posting log |

`meta_ads_get_organic_insights(business_key, days=30)` (meta-ads, read-only) returns Instagram and Facebook post performance, Instagram audience by Pacific hour, a `recommendedWeekdayTime` (`HH:00`) with `recommendationReason`, and `warnings`.

**Register the servers for Claude Code** (from the repo root; `-s local` keeps them out of git):

```bash
claude mcp add google-ads -s local -- .venv/Scripts/python.exe servers/google-ads/mcp_server.py
claude mcp add meta-ads -s local -- .venv/Scripts/python.exe servers/meta-ads/local_stdio.py
claude mcp add tiktok -s local -- .venv/Scripts/python.exe servers/tiktok/local_stdio.py
claude mcp add analytics -s local -- .venv/Scripts/python.exe servers/analytics/mcp_server.py
claude mcp add search-console -s local -- .venv/Scripts/python.exe servers/search-console/mcp_server.py
claude mcp add social -s local -- .venv/Scripts/python.exe servers/social/local_stdio.py
```

`meta-ads`, `tiktok` and `social` need `ADS_MCP_MEDIA_BUCKET` (and AWS credentials) in `.env` for the queue and the log.

**Run it.** In Claude Code say **"schedule new videos"** (the `/schedule-social` skill). It finds new videos, pulls organic insights, writes bilingual captions, assigns slots, shows one batch dry-run preview, and queues everything only after you approve.

**Baseline songs.** A new cut of a baseline song is flagged `possibleRepeat` only while the baseline files stay in the Ready folder; baseline songs are not recorded in the log, so moving those files out removes the check.

**Limits.** Instagram Reels are capped at **300 MB** (the dry run is blocked above it). TikTok drafts carry **no caption**: paste it from `tiktok_list_scheduled` when you tap Post.

## Docs

- `CLAUDE.md` — source of truth for all agents and MCP servers
- `docs/INTEGRATION_CONTRACT.md` — how this repo should interact with `admin-dashboard-rc` and `backend-rc`
- `infrastructure/` — EC2, Nginx, and systemd assets
