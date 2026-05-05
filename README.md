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

All six servers start on ports 8001–8006 with signed-request validation disabled. Ctrl+C stops them all.

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
# Git Bash — requires rsync (not available in plain PowerShell)
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
> scp -i ~/.ssh/rd-mcp-key.pem local-dev-config.json ubuntu@52.21.17.69:/opt/ads-mcp/local-dev-config.json
> ssh -i ~/.ssh/rd-mcp-key.pem ubuntu@52.21.17.69 "cd /opt/ads-mcp && .venv/bin/python scripts/push-secrets.py"
> ssh -i ~/.ssh/rd-mcp-key.pem ubuntu@52.21.17.69 "rm /opt/ads-mcp/local-dev-config.json"
> ```

### 3. Deploy latest code

```bash
# Git Bash — deploy.sh uses rsync which requires bash
bash scripts/deploy.sh
```

This rsync's the repo to `/opt/ads-mcp` on the instance and restarts all systemd services.

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
| analytics | 8003 |
| search-console | 8004 |
| content-agent | 8005 |
| gbp | 8006 |

### Scheduled GBP posts (cron)

Posts queued via `gbp_create_post` with a `scheduled_time` live in `servers/gbp/scheduled_posts.json`. Add this cron on the EC2 instance to publish them:

```bash
# Run once daily at 8am server time
0 8 * * * /opt/ads-mcp/.venv/bin/python /opt/ads-mcp/scripts/run-scheduled-posts.py >> /var/log/ads-mcp/scheduled-posts.log 2>&1
```

---

## Docs

- `CLAUDE.md` — source of truth for all agents and MCP servers
- `docs/INTEGRATION_CONTRACT.md` — how this repo should interact with `admin-dashboard-rc` and `backend-rc`
- `infrastructure/` — EC2, Nginx, and systemd assets
