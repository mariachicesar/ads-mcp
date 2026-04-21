# Integration Contract

## Goal

Define the boundary between:

- `admin-dashboard-rc` — frontend UI
- `backend-rc` — platform API, tenant auth, persistence, approvals, audit trail
- `ads-mcp` — external marketing execution and analysis services

## Recommended responsibility split

### admin-dashboard-rc

Owns:
- Ads and marketing dashboards
- Dry-run previews for campaign changes
- Approval UI for write actions
- History views for recommendations and executed actions
- Content request forms and result presentation

Does not own:
- Google Ads / Meta / GA4 / GSC credentials
- Direct calls to ad-platform APIs
- Long-running MCP processes
- AWS secret retrieval

### backend-rc

Owns:
- JWT auth and tenant authorization
- Mapping app users to allowed tenants/businesses
- Approval records for write actions
- Audit log of proposed and executed actions
- Job records, run status, result snapshots, retry state
- Secure outbound calls from platform context to `ads-mcp`

Suggested backend tables or equivalents:
- `marketing_connection`
- `marketing_action_request`
- `marketing_action_approval`
- `marketing_action_execution`
- `marketing_snapshot`
- `marketing_content_request`

### ads-mcp

Owns:
- Google Ads API integration
- Meta Marketing API integration
- GA4 integration
- Search Console integration
- Content generation prompts and brand context
- Business rule enforcement before write execution
- Dry-run planning for write operations
- Health endpoints and deployment/runtime concerns

## Request flow

### Read/report flow
1. User opens ads or marketing UI in `admin-dashboard-rc`.
2. Frontend calls `backend-rc` endpoint.
3. Backend validates tenant membership and role.
4. Backend calls `ads-mcp` read endpoint or orchestrator action.
5. `ads-mcp` fetches live data and returns normalized output.
6. Backend optionally stores snapshot for history/caching.
7. Frontend renders normalized data.

### Write flow
1. User requests a campaign change in `admin-dashboard-rc`.
2. Frontend sends request to `backend-rc`.
3. Backend stores a pending action request.
4. Backend calls `ads-mcp` in dry-run mode.
5. `ads-mcp` returns proposed changes, business-rule checks, risks, and required confirmations.
6. Frontend shows the dry-run plan to the user.
7. User explicitly approves.
8. Backend records approval and calls `ads-mcp` execute mode.
9. `ads-mcp` re-checks rules, performs the write, and returns execution results.
10. Backend stores execution logs and exposes them to the UI.

## Minimum API shape between backend-rc and ads-mcp

### Read endpoints
- `POST /google-ads/tools/list_accounts`
- `POST /google-ads/tools/get_campaign_performance`
- `POST /google-ads/tools/get_budget_status`
- `POST /analytics/tools/get_traffic_overview`
- `POST /search-console/tools/get_search_performance`
- `POST /content/tools/write_google_ad`

### Write endpoints
All writes should accept:
- `tenantId`
- `businessKey`
- `requestedBy`
- `approvalId`
- `dryRun`
- `ruleContext`
- `payload`

Example:

```json
{
  "tenantId": 12,
  "businessKey": "rnr-electrician",
  "requestedBy": 44,
  "approvalId": "mar_123",
  "dryRun": true,
  "ruleContext": {
    "requiresExplicitApproval": true,
    "protectPausedCampaigns": ["Pasadena-San Marino"],
    "allowGeoChanges": false
  },
  "payload": {
    "campaignName": "Main Search",
    "newDailyBudget": 20
  }
}
```

## Normalized response shape

```json
{
  "ok": true,
  "mode": "dry-run",
  "tool": "update_campaign_budget",
  "businessKey": "rnr-electrician",
  "summary": "Would increase daily budget from $15 to $20",
  "ruleChecks": [
    {
      "rule": "geo-target-lock",
      "passed": true,
      "message": "No geo changes requested"
    }
  ],
  "changes": [
    {
      "field": "campaign.daily_budget",
      "before": 15,
      "after": 20
    }
  ],
  "requiresConfirmation": true,
  "executed": false
}
```

## Security requirements

- `admin-dashboard-rc` should never receive raw ad-platform credentials.
- `ads-mcp` should trust only server-to-server calls from approved origins or signed requests.
- Write execution must require both backend authorization and explicit approval state.
- Business rules from `CLAUDE.md` must be enforced again at execute time, not only at preview time.

## Recommended rollout

1. Build Google Ads MCP first in `ads-mcp`.
2. Add one backend integration route in `backend-rc` for dry-run reads and one for write requests.
3. Add one admin UI screen in `admin-dashboard-rc` for campaign overview + dry-run actions.
4. Only then add Meta, GA4, GSC, and content orchestration.
