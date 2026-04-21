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

## Suggested local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r servers/google-ads/requirements.txt
```

Create per-service virtualenv or one shared env depending on deployment preference.

## Docs

- `CLAUDE.md` — source of truth for all agents and MCP servers
- `docs/INTEGRATION_CONTRACT.md` — how this repo should interact with `admin-dashboard-rc` and `backend-rc`
- `infrastructure/` — EC2, Nginx, and systemd assets
# ads-mcp
