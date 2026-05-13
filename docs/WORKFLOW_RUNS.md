# Workflow Runs — Phase 3 Architecture

This document describes the durable cross-agent workflow execution system added in Phase 3.

---

## Overview

Phase 3 extends the dry-run → approval → execute lifecycle introduced in Phase 1 with **multi-step, durable workflow runs**. A workflow run fans out across multiple specialist MCP servers (Google Ads, Meta Ads, Analytics, Search Console, Content, GBP) in a defined sequence.

**Principle**: ads-mcp is **stateless for persistence**. backend-rc is the system of record for every run, step, and event. ads-mcp validates and returns structured responses; backend-rc persists them.

---

## Data model (backend-rc PostgreSQL)

```
marketing_workflow_run
  id                   bigserial PK
  tenant_id            FK → tenants
  business_key         text
  workflow_type        text
  status               planned | approved | running | completed | failed | cancelled
  objective_json       jsonb
  approval_id          bigint (FK → marketing_action_approval if required)
  started_at / completed_at / cancelled_at
  created_by_user_id

marketing_workflow_step
  id                   bigserial PK
  workflow_run_id      FK → marketing_workflow_run (CASCADE)
  step_order           integer (1-based, unique per run)
  service              google-ads | meta-ads | analytics | search-console | content | gbp
  tool_name            text
  status               planned | queued | running | succeeded | failed | skipped | compensated | cancelled
  request_payload_json jsonb
  response_payload_json jsonb
  retry_count          integer DEFAULT 0
  error_code / error_message
  started_at / completed_at

marketing_workflow_event
  id                   bigserial PK
  workflow_run_id      FK → marketing_workflow_run (CASCADE)
  workflow_step_id     FK → marketing_workflow_step (SET NULL)
  event_type           run_started | run_completed | run_failed | run_cancelled |
                       step_started | step_completed | step_failed | step_retry_queued | step_skipped
  event_payload_json   jsonb
  created_at
```

---

## ads-mcp orchestrator tools

All four tools live in `servers/orchestrator/tools/workflow_runs.py` and are exposed via:

| Transport | File |
|-----------|------|
| HTTP POST | `servers/orchestrator/main.py` (FastAPI, port 8007) |
| FastMCP stdio | `servers/orchestrator/mcp_server.py` |

### `start_workflow_run`
- **Requires** `dryRun=false` and a valid `approvalId`.
- Validates that every step references a supported service.
- Returns an ordered step plan (`status: "queued"`) that backend-rc persists.
- Idempotency: caller passes a `run_id` UUID it generated; orchestrator echoes it back.

### `get_workflow_run`
- Read-only. backend-rc passes its persisted step list; orchestrator derives a progress summary (`total / completed / failed / running / pending`).

### `retry_workflow_step`
- **Requires** `dryRun=false`.
- Validates `run_id` + `step_id` are present.
- Returns `newStatus: "planned"` — backend-rc resets the step and logs a `step_retry_queued` event.

### `cancel_workflow_run`
- **Requires** `dryRun=false`.
- Returns `newStatus: "cancelled"` — backend-rc updates the run and all pending/planned steps.

---

## backend-rc HTTP routes

Base path: `/api/marketing/workflows`  
File: `backend-rc/routes/marketingWorkflows.js`  
Auth: `authMiddleware` + `requireTenantMembership({ anyOfRoles: ["tenant_owner","tenant_manager"] })`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/workflows` | Create a new workflow run (status: `planned`) |
| `POST` | `/workflows/:id/approve` | Approve a planned run (self-approval blocked) |
| `POST` | `/workflows/:id/reject` | Reject and cancel a planned run |
| `POST` | `/workflows/:id/execute` | Start async step execution (fire-and-forget) |
| `POST` | `/workflows/:id/cancel` | Cancel a non-terminal run |
| `GET` | `/workflows` | List runs with step counters (paginated, filterable by status/businessKey/workflowType) |
| `GET` | `/workflows/:id` | Run detail + ordered steps + full event timeline |

The **execute** route calls `lib/workflowExecutor.js` which runs steps sequentially against their respective ads-mcp services, retrying on transient failures and marking the run `failed` if a step exhausts retries. It returns immediately; the client polls `GET /workflows/:id` for progress.

---

## Frontend (`admin-dashboard-rc`)

- Page: `src/app/(admin)/(others-pages)/marketing/workflow-runs/page.tsx`
- Sidebar: "Workflow Runs" under the Marketing nav group
- Features:
  - Status filter chips (All / Running / Planned / Completed / Failed / Cancelled)
  - Expandable run rows with step timeline (connector line + status dot per step)
  - Per-step Retry button (shown only for `failed` or `skipped` steps in non-terminal runs)
  - Per-run Cancel button (shown only for non-terminal runs)
  - Auto-refresh every 10 s while any run has `status = "running"`
  - Paginated (20 per page)

---

## Sequence: create → approve → execute

```
Browser → POST /api/marketing/workflows  (workflowType, businessKey, objective, steps[])
  backend-rc → BEGIN TRANSACTION
  backend-rc → INSERT marketing_workflow_run (status='planned')
  backend-rc → INSERT marketing_workflow_step × N
  backend-rc → INSERT marketing_workflow_event ('workflow.created')
  backend-rc → COMMIT
  backend-rc → 201 { workflowRun, steps }

Browser → POST /api/marketing/workflows/:id/approve
  backend-rc → validate status='planned', block self-approval
  backend-rc → UPDATE run status='approved'
  backend-rc → INSERT event ('workflow.approved')
  backend-rc → 200 { workflowRun }

Browser → POST /api/marketing/workflows/:id/execute
  backend-rc → validate status='approved' (or 'planned' if require_approval=false)
  backend-rc → fire-and-forget: executeWorkflowRunAsync(runId)
    workflowExecutor → UPDATE run status='running'
    workflowExecutor → for each step in step_order:
        workflowExecutor → UPDATE step status='running'
        workflowExecutor → callAdsMcpWithRetry(service, toolName, payload)
        workflowExecutor → UPDATE step status='succeeded'|'failed'
        workflowExecutor → INSERT event ('step.completed'|'step.failed')
        [on failure] workflowExecutor → halt remaining steps as 'skipped'
    workflowExecutor → UPDATE run status='completed'|'failed'
  backend-rc → 200 { workflowRun, message: "Execution started. Poll GET /workflows/:id" }
```

---

## Supported services

| Slug | Port | ads-mcp server |
|------|------|----------------|
| `google-ads` | 8001 | `servers/google-ads/` |
| `meta-ads` | 8002 | `servers/meta-ads/` |
| `analytics` | 8003 | `servers/analytics/` |
| `search-console` | 8004 | `servers/search-console/` |
| `content` | 8005 | `servers/content-agent/` |
| `gbp` | 8006 | `servers/gbp/` |
| `orchestrator` | 8007 | `servers/orchestrator/` |

---

## Related docs

- [INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md) — signing scheme, dry-run contract, approval flow
- [BACKEND_RC_PHASE1_SPEC.md](BACKEND_RC_PHASE1_SPEC.md) — Phase 1 single-action request/approval tables
