# Backend RC Phase 1 Spec

## Goal

Define the minimum durable backend implementation needed in `backend-rc` to support the current `ads-mcp` Phase 1 foundations and complete one safe end-to-end workflow.

This spec assumes:

- `backend-rc` is the system of record for tenant authorization, approvals, execution logs, content history, and snapshots.
- `ads-mcp` remains stateless for durable workflow state.
- All writes follow `dry-run -> approval -> execute`.
- `ads-mcp` is called only from `backend-rc` using signed server-to-server requests.

## Phase 1 Outcome

Phase 1 is complete when `backend-rc` can:

1. accept a tenant-scoped marketing request
2. call `ads-mcp` for a dry-run preview
3. store the preview and approval record durably
4. approve or reject the action explicitly
5. call `ads-mcp` execute mode after approval
6. store execution results and expose status/history to the UI

## Required Durable Tables

Use your normal backend migration flow. The exact ORM or SQL dialect can vary, but the logical models should remain stable.

### marketing_connection

Purpose: maps a tenant business to one connected marketing platform account.

Required fields:

- `id`
- `tenant_id`
- `business_key`
- `platform` — `google-ads`, `meta-ads`, `analytics`, `search-console`, `content`
- `external_account_id`
- `external_manager_account_id` nullable
- `status` — `pending`, `active`, `disabled`, `error`
- `display_name` nullable
- `metadata_json` nullable
- `created_at`
- `updated_at`

Required constraints:

- unique on `tenant_id`, `business_key`, `platform`

### marketing_action_request

Purpose: durable record of one requested marketing action before or during approval.

Required fields:

- `id`
- `tenant_id`
- `business_key`
- `platform`
- `tool_name`
- `requested_by_user_id`
- `status` — `draft`, `dry_run_created`, `pending_approval`, `approved`, `rejected`, `executing`, `executed`, `failed`, `expired`
- `request_payload_json`
- `rule_context_json` nullable
- `dry_run_response_json` nullable
- `request_id` — backend correlation ID used toward `ads-mcp`
- `idempotency_key` nullable
- `expires_at` nullable
- `created_at`
- `updated_at`

Required constraints:

- index on `tenant_id`, `business_key`, `status`
- unique on non-null `idempotency_key` if you use one

### marketing_action_approval

Purpose: approval decision state for a requested action.

Required fields:

- `id`
- `action_request_id`
- `approval_status` — `pending`, `approved`, `rejected`, `expired`, `cancelled`
- `requested_by_user_id`
- `approved_by_user_id` nullable
- `rejected_by_user_id` nullable
- `decision_reason` nullable
- `approved_at` nullable
- `rejected_at` nullable
- `expires_at` nullable
- `created_at`
- `updated_at`

Required constraints:

- one active approval record per action request in Phase 1

### marketing_action_execution

Purpose: execution attempt log for approved writes.

Required fields:

- `id`
- `action_request_id`
- `approval_id`
- `execution_status` — `queued`, `running`, `succeeded`, `failed`
- `executed_by_user_id` nullable
- `request_id`
- `ads_mcp_response_json` nullable
- `error_code` nullable
- `error_message` nullable
- `started_at` nullable
- `completed_at` nullable
- `created_at`

Required constraints:

- index on `action_request_id`
- index on `approval_id`

### marketing_snapshot

Purpose: cached read result or preview snapshot for history and reduced repeated API calls.

Required fields:

- `id`
- `tenant_id`
- `business_key`
- `platform`
- `snapshot_type`
- `snapshot_key`
- `data_json`
- `freshness_state` — `live`, `cached`, `stale`
- `captured_at`
- `expires_at` nullable

Recommended usage:

- store campaign overview snapshots
- store dry-run response snapshots for later UI replay

### marketing_content_request

Purpose: durable record of content draft requests and revision history.

Required fields:

- `id`
- `tenant_id`
- `business_key`
- `requested_by_user_id`
- `content_type`
- `input_json`
- `draft_response_json` nullable
- `status` — `draft_created`, `selected`, `rejected`, `publish_pending_approval`, `published`
- `selected_variant_key` nullable
- `created_at`
- `updated_at`

## Required State Machine

### marketing_action_request status transitions

Allowed transitions:

- `draft -> dry_run_created`
- `dry_run_created -> pending_approval`
- `pending_approval -> approved`
- `pending_approval -> rejected`
- `approved -> executing`
- `executing -> executed`
- `executing -> failed`
- `dry_run_created -> expired`
- `pending_approval -> expired`

Required rules:

- execute is never allowed unless request status is `approved`
- rejection is terminal for Phase 1
- expired requests must require a fresh dry-run before execution

### marketing_action_approval status transitions

Allowed transitions:

- `pending -> approved`
- `pending -> rejected`
- `pending -> expired`
- `pending -> cancelled`

Required rules:

- the approver should not be the same user as the requester unless you explicitly allow self-approval
- approval expiration should invalidate execute calls
- `approval_id` passed to `ads-mcp` must refer to an `approved` record that belongs to the same action request

## Backend RC Endpoint Responsibilities

### Read endpoints

Backend responsibilities:

- authorize tenant access
- load the correct `business_key`
- sign the outbound `ads-mcp` request
- optionally store a `marketing_snapshot`
- return normalized data to the frontend

### Dry-run write endpoints

Backend responsibilities:

- validate user authorization
- create `marketing_action_request` in `draft`
- call `ads-mcp` with `dryRun: true`
- store returned preview in `dry_run_response_json`
- transition request to `dry_run_created`
- create `marketing_action_approval` in `pending`
- transition request to `pending_approval`

### Approval endpoints

Backend responsibilities:

- verify the approver is allowed to approve
- update `marketing_action_approval`
- transition the linked `marketing_action_request` to `approved` or `rejected`

### Execute endpoints

Backend responsibilities:

- verify linked approval is `approved` and not expired
- create `marketing_action_execution` in `queued` or `running`
- call `ads-mcp` with `dryRun: false` and the `approvalId`
- persist the full MCP response
- transition request and execution records to `executed` or `failed`

## Outbound MCP Request Requirements

Every outbound signed request from `backend-rc` to `ads-mcp` should include:

- `tenantId`
- `businessKey`
- `requestedBy`
- `approvalId` on execute calls
- `dryRun`
- `payload`
- `ruleContext` if backend policy needs to narrow behavior
- `requestMeta` with backend correlation info where useful

Recommended `requestMeta` fields:

- `actionRequestId`
- `approvalId`
- `executionId`
- `uiSurface`
- `source` — `dashboard`, `internal-job`, `api`

## Idempotency And Retries

Phase 1 recommendations:

- generate one backend `request_id` per outbound MCP request
- use one logical `idempotency_key` per user action request if the UI can retry submits
- retries of the same logical execute action should create new signed envelopes but remain linked to the same action request and approval record
- execution retries should create additional `marketing_action_execution` records rather than overwriting previous attempts

## Approval Expiration

Recommended Phase 1 behavior:

- dry-runs and approvals should expire after a bounded window
- once expired, execute must fail and require a new dry-run
- backend-rc should own expiration checks before calling execute

## Suggested Backend RC Implementation Order

1. create the durable schema above
2. implement dry-run request creation and persistence
3. implement approval endpoints and transitions
4. implement execute endpoint and execution logging
5. add snapshot persistence for read flows and previews
6. add tenant onboarding that creates the `marketing_connection` record and corresponding runtime config outside this repo

## Phase 1 Acceptance

`backend-rc` Phase 1 is complete when one Google Ads budget change can move through:

1. dry-run request creation
2. preview persistence
3. approval creation
4. approval decision
5. execute request
6. execution persistence
7. history retrieval for the UI