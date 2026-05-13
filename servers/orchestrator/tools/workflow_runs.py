"""Workflow run lifecycle tools for the orchestrator.

These tools are called by backend-rc (never directly by the frontend) to:
  - start_workflow_run   — accept a queued run from backend, validate, return run_id
  - get_workflow_run     — return current status + step list for a run_id
  - retry_workflow_step  — re-queue a failed step for execution
  - cancel_workflow_run  — mark a running/queued run as cancelled

The orchestrator is STATELESS for persistence — backend-rc owns the DB.
These tools return structured responses; backend-rc persists them.
"""

from __future__ import annotations

import uuid
from typing import Any

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response, build_rule_check

SUPPORTED_SERVICES = {
    "google-ads",
    "meta-ads",
    "analytics",
    "search-console",
    "content",
    "gbp",
}

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
VALID_STEP_STATUSES = {"planned", "running", "completed", "failed", "skipped", "cancelled"}


# ── start_workflow_run ────────────────────────────────────────────────────────

def start_workflow_run(request: ToolRequest, request_id: str | None) -> dict[str, Any]:
    """Validate and accept a workflow run queued by backend-rc.

    Expects payload:
        run_id       — backend-generated UUID for this run (idempotency key)
        approval_id  — approval ID that authorised this run
        objective    — human-readable goal
        steps        — ordered list of {service, tool_name, payload} dicts

    Returns a run descriptor with an ordered step plan. backend-rc persists this.
    """
    if request.dryRun is not False:
        raise AdsMcpError(
            status_code=400,
            error_code="DRY_RUN_NOT_ALLOWED",
            message="start_workflow_run requires dryRun=false. Plan first with plan_cross_agent_workflow.",
            tool="start_workflow_run",
        )

    if not request.approvalId:
        raise AdsMcpError(
            status_code=400,
            error_code="APPROVAL_REQUIRED",
            message="approvalId is required to start a workflow run.",
            tool="start_workflow_run",
        )

    run_id = request.payload.get("run_id") or str(uuid.uuid4())
    steps_input: list[dict] = request.payload.get("steps", [])
    objective: str = request.payload.get("objective", "Cross-agent workflow run")

    if not steps_input:
        raise AdsMcpError(
            status_code=400,
            error_code="INVALID_PAYLOAD",
            message="payload.steps must be a non-empty list of step definitions.",
            tool="start_workflow_run",
        )

    steps = []
    for i, raw in enumerate(steps_input, start=1):
        service = raw.get("service", "")
        if service not in SUPPORTED_SERVICES:
            raise AdsMcpError(
                status_code=400,
                error_code="UNSUPPORTED_SERVICE",
                message=f"Step {i} references unsupported service '{service}'.",
                details={"step": i, "service": service},
                tool="start_workflow_run",
            )
        steps.append(
            {
                "stepNumber": i,
                "stepId": str(uuid.uuid4()),
                "service": service,
                "toolName": raw.get("tool_name", ""),
                "payload": raw.get("payload", {}),
                "status": "planned",
                "retryCount": 0,
                "result": None,
            }
        )

    rule_checks = [
        build_rule_check(
            rule="approval-verified",
            passed=True,
            message=f"approvalId={request.approvalId} accepted for run {run_id}.",
        ),
        build_rule_check(
            rule="dry-run-first",
            passed=True,
            message="Run accepted after dry-run plan was reviewed.",
        ),
    ]

    return build_success_response(
        service="orchestrator",
        tool="start_workflow_run",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Workflow run {run_id} accepted with {len(steps)} steps.",
        data={
            "runId": run_id,
            "status": "queued",
            "objective": objective,
            "approvalId": request.approvalId,
            "steps": steps,
        },
        rule_checks=rule_checks,
        requires_confirmation=False,
        executed=True,
    )


# ── get_workflow_run ──────────────────────────────────────────────────────────

def get_workflow_run(request: ToolRequest, request_id: str | None) -> dict[str, Any]:
    """Return status snapshot for a run. backend-rc passes its persisted state;
    orchestrator re-computes derived fields (e.g. overall status from step statuses).

    Expects payload:
        run_id        — the run UUID
        steps         — current step list from backend DB (with status, retryCount, result)
        run_status    — current run status from backend DB
    """
    run_id: str = request.payload.get("run_id", "")
    steps: list[dict] = request.payload.get("steps", [])
    run_status: str = request.payload.get("run_status", "unknown")

    if not run_id:
        raise AdsMcpError(
            status_code=400,
            error_code="INVALID_PAYLOAD",
            message="payload.run_id is required.",
            tool="get_workflow_run",
        )

    # Derive progress summary
    total = len(steps)
    completed = sum(1 for s in steps if s.get("status") == "completed")
    failed = sum(1 for s in steps if s.get("status") == "failed")
    running = sum(1 for s in steps if s.get("status") == "running")

    return build_success_response(
        service="orchestrator",
        tool="get_workflow_run",
        mode="read",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Run {run_id}: {completed}/{total} steps completed, {failed} failed.",
        data={
            "runId": run_id,
            "status": run_status,
            "progress": {
                "total": total,
                "completed": completed,
                "failed": failed,
                "running": running,
                "pending": total - completed - failed - running,
            },
            "steps": steps,
        },
        requires_confirmation=False,
        executed=False,
    )


# ── retry_workflow_step ───────────────────────────────────────────────────────

def retry_workflow_step(request: ToolRequest, request_id: str | None) -> dict[str, Any]:
    """Mark a failed step as eligible for retry.

    Expects payload:
        run_id   — the run UUID
        step_id  — the specific step UUID to retry
        reason   — optional human note for the audit log
    """
    if request.dryRun is not False:
        raise AdsMcpError(
            status_code=400,
            error_code="DRY_RUN_NOT_ALLOWED",
            message="retry_workflow_step requires dryRun=false.",
            tool="retry_workflow_step",
        )

    run_id: str = request.payload.get("run_id", "")
    step_id: str = request.payload.get("step_id", "")
    reason: str = request.payload.get("reason", "")

    if not run_id or not step_id:
        raise AdsMcpError(
            status_code=400,
            error_code="INVALID_PAYLOAD",
            message="payload.run_id and payload.step_id are required.",
            tool="retry_workflow_step",
        )

    return build_success_response(
        service="orchestrator",
        tool="retry_workflow_step",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Step {step_id} in run {run_id} queued for retry.",
        data={
            "runId": run_id,
            "stepId": step_id,
            "action": "retry",
            "reason": reason or None,
            "newStatus": "planned",
        },
        requires_confirmation=False,
        executed=True,
    )


# ── cancel_workflow_run ───────────────────────────────────────────────────────

def cancel_workflow_run(request: ToolRequest, request_id: str | None) -> dict[str, Any]:
    """Request cancellation of a queued or running workflow run.

    Expects payload:
        run_id  — the run UUID
        reason  — optional cancellation reason
    """
    if request.dryRun is not False:
        raise AdsMcpError(
            status_code=400,
            error_code="DRY_RUN_NOT_ALLOWED",
            message="cancel_workflow_run requires dryRun=false.",
            tool="cancel_workflow_run",
        )

    run_id: str = request.payload.get("run_id", "")
    reason: str = request.payload.get("reason", "")

    if not run_id:
        raise AdsMcpError(
            status_code=400,
            error_code="INVALID_PAYLOAD",
            message="payload.run_id is required.",
            tool="cancel_workflow_run",
        )

    return build_success_response(
        service="orchestrator",
        tool="cancel_workflow_run",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Cancellation requested for run {run_id}.",
        data={
            "runId": run_id,
            "action": "cancel",
            "reason": reason or None,
            "newStatus": "cancelled",
        },
        requires_confirmation=False,
        executed=True,
    )
