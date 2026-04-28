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
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\activate           # Windows
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
export ADS_MCP_GOOGLE_ADS_CONFIGS_JSON=$(cat local-dev-config.json)
export ADS_MCP_REQUIRE_SIGNED_REQUESTS=false
```

**5. Run all servers**

```bash
python scripts/dev-run.py
```

All five servers start on ports 8001–8005 with signed-request validation disabled. Ctrl+C stops them all.

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
    }
  }
}
```

On **Windows**, use the `.venv\Scripts\python.exe` path and backslashes in paths, or use forward slashes — both work in JSON. Restart Claude Desktop after editing the config.

---

## Docs

- `CLAUDE.md` — source of truth for all agents and MCP servers
- `docs/INTEGRATION_CONTRACT.md` — how this repo should interact with `admin-dashboard-rc` and `backend-rc`
- `infrastructure/` — EC2, Nginx, and systemd assets
# ads-mcp
