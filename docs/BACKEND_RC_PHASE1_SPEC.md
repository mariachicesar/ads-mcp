# Backend RC Phase 1 Spec

## Goal

Define the minimum durable backend implementation needed in `backend-rc` to support the current `ads-mcp` Phase 1 foundations and complete one safe end-to-end workflow.

This spec assumes:

- `backend-rc` is the system of record for tenant authorization, approvals, execution logs, content history, and snapshots.
- `ads-mcp` remains stateless for durable workflow state.
- All writes follow `dry-run -> approval -> execute`.
- `ads-mcp` is called only from `backend-rc` using signed server-to-server requests.

## Implementation Checklist (backend-rc)

Use this as the execution checklist to complete the backend side of Phase 1.

### A. Data model and migrations

- [ ] Create `marketing_connection` with unique `(tenant_id, business_key, platform)`.
- [ ] Create `marketing_action_request` with request payload, dry-run response, status, and correlation `request_id`.
- [ ] Create `marketing_action_approval` linked to action request and enforce one active approval per request.
- [ ] Create `marketing_action_execution` linked to request + approval and store raw `ads_mcp_response_json`.
- [ ] Create `marketing_snapshot` for read/dry-run snapshots with freshness metadata.
- [ ] Create `marketing_content_request` for content draft lifecycle.
- [ ] Add indexes for frequent filters: `(tenant_id, business_key, status)` and foreign key indexes.

### B. State machine guards

- [ ] Enforce allowed transitions in service layer for `marketing_action_request`.
- [ ] Enforce allowed transitions in service layer for `marketing_action_approval`.
- [ ] Block execute unless request status is `approved` and approval status is `approved`.
- [ ] Block execute if approval expired.
- [ ] Reject execute if `approval_id` does not belong to the same action request.

### C. Security and request signing

- [ ] Add outbound signer middleware/client for all backend -> ads-mcp requests.
- [ ] Include `tenantId`, `businessKey`, `requestedBy`, `dryRun`, `payload` in every write request.
- [ ] Include `approvalId` on execute calls.
- [ ] Ensure backend never exposes ad-platform credentials to frontend.

### D. Endpoints (minimum)

- [ ] `POST /marketing/:platform/:businessKey/read/...` pass-through reads with tenant authorization.
- [ ] `POST /marketing/:platform/:businessKey/actions/dry-run` create request in `draft`, call ads-mcp with `dryRun: true`, persist preview.
- [ ] `POST /marketing/actions/:actionRequestId/approve` and reject endpoint with approver validation.
- [ ] `POST /marketing/actions/:actionRequestId/execute` validate approval and call ads-mcp with `dryRun: false`.
- [ ] `GET /marketing/actions/:actionRequestId` and list endpoints for history/status.

### E. Idempotency and observability

- [ ] Support `idempotency_key` for dry-run and execute endpoints.
- [ ] Persist full request/response payloads for audit.
- [ ] Add structured logs with `request_id`, `action_request_id`, `approval_id`, `tenant_id`.
- [ ] Add retry-safe behavior for network/transient failures.

### F. Phase 1 acceptance tests

- [ ] Dry-run write request stores preview and creates pending approval.
- [ ] Unapproved execute attempt fails with clear error.
- [ ] Approved execute attempt updates status to `executed` on success.
- [ ] Failed execute attempt updates status to `failed` and stores error payload.
- [ ] Expired approval prevents execution.
- [ ] Cross-tenant access is denied for reads, dry-run, and execute.

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

---

# Backend RC Phase 2 Spec

## Goal

Move from one validated end-to-end flow to a production-grade multi-platform backend integration with operational safety, policy controls, and observability.

## Phase 2 Outcomes

Phase 2 is complete when `backend-rc` can:

1. run dry-run -> approval -> execute for Google Ads, Meta, GA4, Search Console, Content, and GBP
2. enforce request signing and approval policies for every write operation
3. provide reliable retry and idempotency behavior for transient failures
4. expose complete action history and execution logs for tenant users and internal operators
5. alert on failed executions and degraded MCP dependencies

## Phase 2 Implementation Checklist

### A. Platform coverage expansion

- [ ] Add backend route mappings for Meta, GA4, Search Console, Content, and GBP tools.
- [ ] Normalize cross-platform response envelopes to one frontend contract.
- [ ] Persist platform-specific snapshots in `marketing_snapshot` with stable `snapshot_type` values.

### B. Security hardening

- [ ] Enforce signed outbound requests to `ads-mcp` for all read and write calls.
- [ ] Reject execute endpoints unless linked approval is valid and unexpired.
- [ ] Add role-based policy checks (requester, approver, executor).
- [ ] Add replay protection for signed envelopes (nonce or timestamp validation strategy).

### C. Reliability and idempotency

- [ ] Implement idempotency-key semantics for both dry-run and execute endpoints.
- [ ] Add retry policy with backoff and max-attempt limits for MCP network errors.
- [ ] Create explicit terminal failure reasons (policy denied, expired approval, upstream timeout, MCP error).
- [ ] Ensure retries create new `marketing_action_execution` rows without mutating prior attempts.

### D. Observability and auditability

- [ ] Emit structured logs with `tenant_id`, `action_request_id`, `approval_id`, `execution_id`, `request_id`.
- [ ] Add metrics: dry-runs, approvals, executes, failures, latency, retries.
- [ ] Add alerting thresholds for sustained failure rate and MCP unavailability.
- [ ] Provide operator view/query path for failed executions and pending approvals.

### E. Dashboard integration

- [ ] Expose list/filter endpoints for pending approvals and historical actions.
- [ ] Return normalized dry-run diff payloads ready for UI rendering.
- [ ] Return execution progress states suitable for polling or event updates.

## Phase 2 Acceptance Tests

- [ ] Each supported platform completes dry-run -> approval -> execute in integration tests.
- [ ] Expired approval blocks execute with deterministic error code.
- [ ] Duplicate execute with same idempotency key does not create duplicate side effects.
- [ ] Failed upstream dependency triggers retry policy and observable failure logs.
- [ ] Tenant isolation is enforced across all platforms and endpoints.

---

# Backend RC Phase 3 Spec

## Goal

Enable durable cross-agent orchestration where backend-managed workflows coordinate multiple MCP services with step-level tracking, rollback policy, and governance.

## Phase 3 Outcomes

Phase 3 is complete when `backend-rc` can:

1. persist and execute multi-step orchestration plans from the orchestrator service
2. track per-step status, retries, and outputs across heterogeneous MCP services
3. enforce approval and policy gates at both workflow and step levels
4. recover safely from partial failures using defined retry/compensation rules
5. provide complete workflow history and analytics to dashboard users

## Phase 3 Durable Additions

Add orchestration-specific durable entities (or equivalent models):

- `marketing_workflow_run`
- `marketing_workflow_step`
- `marketing_workflow_event`

Minimum fields:

### marketing_workflow_run

- `id`
- `tenant_id`
- `business_key`
- `workflow_type`
- `status` — `planned`, `approved`, `running`, `completed`, `failed`, `cancelled`
- `objective_json`
- `approval_id` nullable
- `started_at` nullable
- `completed_at` nullable
- `created_at`

### marketing_workflow_step

- `id`
- `workflow_run_id`
- `step_order`
- `service` — `google-ads`, `meta-ads`, `analytics`, `search-console`, `content`, `gbp`
- `tool_name`
- `status` — `planned`, `queued`, `running`, `succeeded`, `failed`, `skipped`, `compensated`
- `request_payload_json`
- `response_payload_json` nullable
- `retry_count`
- `error_code` nullable
- `error_message` nullable
- `started_at` nullable
- `completed_at` nullable

### marketing_workflow_event

- `id`
- `workflow_run_id`
- `workflow_step_id` nullable
- `event_type`
- `event_payload_json`
- `created_at`

## Phase 3 Orchestration Checklist

### A. Planning and approval

- [ ] Persist orchestrator plan output as a workflow run with ordered steps.
- [ ] Require explicit approval for workflow execution when any step is write-capable.
- [ ] Support policy-based step suppression (forbidden tools/platforms by role or tenant policy).

### B. Execution engine

- [ ] Build backend worker/runner for step-by-step execution.
- [ ] Enforce step dependencies and halt/continue strategy configuration.
- [ ] Capture per-step request/response and correlation IDs.
- [ ] Support resumable workflow runs after transient interruption.

### C. Failure handling

- [ ] Implement per-step retry strategy with bounded attempts.
- [ ] Define compensation policy per workflow type.
- [ ] Mark unrecoverable failures with actionable error codes and operator guidance.

### D. Governance and visibility

- [ ] Add role-based authorization for creating, approving, cancelling, and retrying workflow runs.
- [ ] Expose workflow run timeline endpoints for dashboard UI.
- [ ] Add metrics for workflow duration, step failure hotspots, and retry rates.

## Phase 3 Acceptance Tests

- [ ] A multi-step workflow across at least three services reaches `completed` with full step logs.
- [ ] Mid-workflow step failure triggers retry and then terminal failure when retry budget is exhausted.
- [ ] Cancellation during execution leaves workflow in consistent terminal state.
- [ ] Compensation path executes for workflow types that define rollback behavior.
- [ ] Tenant and role policies are enforced across workflow create/approve/execute operations.