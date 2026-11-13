# Credentials, Config and Troubleshooting

Source of truth for where credentials live, how servers find them, and the known
failure modes found while running the MCP servers locally and on EC2.
Last updated: 2026-11-12.

---

## 1. Where credentials live

**AWS Secrets Manager is the only place credentials are stored.** Local MCP client
configs (Claude Desktop, Claude Code) must not contain credentials.

| What | Where |
|------|-------|
| Per-client, per-platform credentials | Secrets Manager `/ads-mcp/{business_key}/{platform}/config` (region `us-east-1`) |
| Platforms | `google-ads`, `analytics`, `search-console`, `gbp`, `meta-ads`, `tiktok`, `youtube` |
| Business keys | `rnr-electrician`, `gq-painting`, `el-cuis` (see `clients/*.json`) |
| Local AWS access (for local servers) | `~/.aws/credentials` and `~/.aws/config` (`region = us-east-1`) |
| EC2 AWS access | IAM role on the instance (`secretsmanager:GetSecretValue`) |

The Google `refresh_token` in Secrets Manager carries the scopes
`adwords`, `analytics.readonly`, `webmasters.readonly`, `business.manage`,
`userinfo.email`, `openid`. One token serves Ads, GA4, Search Console and GBP.

### Rules

- Never paste credentials into `claude_desktop_config.json`, `~/.claude.json`,
  `.mcp.json` or any committed file. An MCP server entry only needs `command`, `args`
  and non-secret env such as `ADS_MCP_REQUIRE_SIGNED_REQUESTS=false`.
- Never keep AWS root access keys on disk, not even in a comment in `.env`.
- To rotate a credential, update the Secrets Manager secret only. Running servers cache
  secrets in memory, so restart or reconnect them afterwards (`/mcp` in Claude Code,
  restart Claude Desktop, `systemctl restart` on EC2).
- When inspecting configs, mask by **value**, not by key name. The `*_CONFIGS_JSON`
  env vars have harmless-looking names but hold full secrets.

---

## 2. How a server resolves its config

`shared/runtime_config.py::load_platform_runtime_config`:

1. Env var `ADS_MCP_{PLATFORM}_CONFIGS_JSON` (e.g. `ADS_MCP_GOOGLE_ADS_CONFIGS_JSON`),
   a JSON object keyed by business key. **If set, it wins over Secrets Manager.**
2. Otherwise Secrets Manager `/ads-mcp/{business_key}/{platform}/config`
   (`shared/secrets.py::get_platform_config`). boto3 uses `AWS_REGION` if set, else the
   region in `~/.aws/config`.
3. If a client manifest exists (`clients/{business_key}.json`), its platform settings are
   merged in and `required_keys` are checked.

Because step 1 overrides step 2, an old inline copy in a client config silently shadows
the current secret. That happened on 2026-11-12: Claude Desktop held older Google refresh
tokens (missing `business.manage`) while Secrets Manager had the current ones. The inline
copies were removed and all servers now read Secrets Manager.

The `*_CONFIGS_JSON` env vars are only for isolated local dev (`scripts/dev-run.py` with a
gitignored `local-dev-config.json`) and for tests.

---

## 3. MCP server registration

Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`) and Claude Code
(`claude mcp list`) register servers separately. A server built in this repo is not
available in a client until it is registered there.

- `youtube` and `gbp` were built but unregistered in Claude Code until 2026-11-12. They are
  now registered (local scope). `gbp` needs `ADS_MCP_MEDIA_BUCKET` in its env.
- After changing server code or `.env`, reconnect the server (`/mcp` → reconnect) or
  restart the client. Servers do not reload on their own.

---

## 4. `.env` pitfalls

- Every variable must be on its own line. On 2026-11-12 `META_API_VERSION=v23.0` and
  `META_INSTAGRAM_USER_ID=...` had merged onto one line. Every Meta Graph call then went to
  a broken URL and looked like an expired-token or "Instagram not linked" error.
  Check with: `grep -nE '^META_(API_VERSION|INSTAGRAM_USER_ID)=' .env` (two lines expected).
- The meta-ads server reads `.env` only at startup. Restart or reconnect it after editing.

---

## 5. Checking that EC2 runs the latest code

`scripts/deploy.sh` copies the working tree to `/opt/ads-mcp` (no `.git` there), so
compare file hashes.

**Windows gotcha:** the local working copy has CRLF line endings, while EC2 has LF. Hashing
the local file gives a false mismatch. Hash the git version instead:

```bash
git show HEAD:scripts/run-scheduled-social.py | sha256sum
ssh -i ~/.ssh/rd-mcp-key.pem ubuntu@52.21.17.69 "sha256sum /opt/ads-mcp/scripts/run-scheduled-social.py"
```

Matching hashes mean EC2 has that commit's runner. Instagram, YouTube verification, GBP
and TikTok posts are published by this runner on EC2. If it is out of date, posts queue but
never go out.

---

## 6. ffmpeg / ffprobe inside MCP servers

MCP stdio servers use stdin as the JSON-RPC pipe. A child process that inherits stdin can
hang or steal protocol bytes. ffmpeg reads stdin for keyboard commands, so frame grabs hung
until the 60 s timeout. Every GBP dry run then failed with a blocking `gbp-frame` rule,
although the same command took 0.4 s in a shell.

Fixed in `shared/video_probe.py` (commit `2831993`): both subprocesses run with
`stdin=subprocess.DEVNULL`, and ffmpeg also gets `-nostdin`. **Any new subprocess call in
an MCP server must pass `stdin=subprocess.DEVNULL`.**

ffmpeg on the local machine: winget package `Gyan.FFmpeg` (must be on `PATH`).

---

## 7. Tests and the live social profile

Tests must not read live config. The `social_profile_dir` fixture (`tests/conftest.py`) used
to copy `social/profiles/el-cuis.json`, so enabling YouTube and GBP in production
(commit `6e4ecfa`) broke 15 tests that expect three platforms.

- Tests now use a frozen copy: `tests/fixtures/social_profiles/el-cuis.json`
  (three platforms: facebook, instagram, tiktok). Change it only when a test needs it.
- `tests/shared/test_live_social_profiles.py` checks that every real profile in
  `social/profiles/` is well-formed (business key, known platforms, phone, cities, slots,
  `platformStarts` only for enabled platforms).
- Edit the live profile freely for operations. The tests stay stable.

---

## 8. Pre-flight checklist before a social posting batch

Run read-only checks for every known blocker **before** any dry run:

1. `.env` Meta lines are separate (section 4), and meta-ads was restarted after any edit.
2. `meta_ads_get_organic_insights(business_key="el-cuis")` returns Instagram posts.
   This proves the token, the API version and the Instagram link.
3. `youtube` and `gbp` tools are loaded in the client (section 3).
4. The EC2 runner hash matches `HEAD` (section 5).
5. ffmpeg is installed. The GBP dry run returns a `framePath`. Look at the image.

Then follow `.claude/skills/schedule-social/SKILL.md`: dry runs, one batch approval, and a
fresh dry run plus execute per platform.

---

## 9. Open follow-ups (as of 2026-11-12)

- Rotate the Google OAuth client secret and refresh tokens. They were printed in a local
  session transcript while the Desktop config was being inspected. Update the Secrets
  Manager secrets after rotating.
- Rotate the AWS root access key that was in a `.env` comment, then delete the comment.
- `ANTHROPIC_API_KEY` is still inline in the Claude Desktop `content-agent` entry.
  Move it to Secrets Manager or a user environment variable.
