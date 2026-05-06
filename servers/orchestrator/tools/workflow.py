from __future__ import annotations

from typing import Any

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_success_response, build_rule_check


SUPPORTED_SERVICES = {
    "google-ads": "/google-ads/tools",
    "meta-ads": "/meta-ads/tools",
    "analytics": "/analytics/tools",
    "search-console": "/search-console/tools",
    "content": "/content/tools",
    "gbp": "/gbp/tools",
}


def _resolve_base_url(request: ToolRequest) -> str:
    base_url = (
        request.requestMeta.get("mcpBaseUrl")
        or request.ruleContext.get("mcpBaseUrl")
        or "https://mcp.rctechbridge.com"
    )
    return str(base_url).rstrip("/")


def _resolve_services(request: ToolRequest) -> list[str]:
    requested = request.payload.get("services")
    if not requested:
        return list(SUPPORTED_SERVICES.keys())
    if not isinstance(requested, list):
        raise AdsMcpError(
            status_code=400,
            error_code="INVALID_PAYLOAD",
            message="payload.services must be a list of service keys.",
            tool="plan_cross_agent_workflow",
        )

    unknown = [svc for svc in requested if svc not in SUPPORTED_SERVICES]
    if unknown:
        raise AdsMcpError(
            status_code=400,
            error_code="UNSUPPORTED_SERVICE",
            message="One or more requested services are not supported by orchestrator.",
            details={"unknownServices": unknown},
            tool="plan_cross_agent_workflow",
        )
    return requested


def plan_cross_agent_workflow(request: ToolRequest, request_id: str | None) -> dict[str, Any]:
    services = _resolve_services(request)
    base_url = _resolve_base_url(request)
    objective = request.payload.get("objective", "Generate a multi-service marketing action plan")

    steps: list[dict[str, Any]] = []
    for index, service in enumerate(services, start=1):
        steps.append(
            {
                "step": index,
                "service": service,
                "endpointBase": f"{base_url}{SUPPORTED_SERVICES[service]}",
                "mode": "read",
                "status": "planned",
            }
        )

    rule_checks = [
        build_rule_check(
            rule="dry-run-first",
            passed=True,
            message="Workflow plan generated in dry-run mode.",
        ),
        build_rule_check(
            rule="explicit-approval-required",
            passed=True,
            message="Execution requires explicit approvalId and dryRun=false.",
        ),
    ]

    return build_success_response(
        service="orchestrator",
        tool="plan_cross_agent_workflow",
        mode="dry-run",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Generated workflow plan with {len(steps)} service steps.",
        data={
            "objective": objective,
            "services": services,
            "steps": steps,
            "nextAction": "Review plan and call execute_cross_agent_workflow with approvalId.",
        },
        rule_checks=rule_checks,
        requires_confirmation=True,
        executed=False,
    )


def execute_cross_agent_workflow(request: ToolRequest, request_id: str | None) -> dict[str, Any]:
    if request.dryRun is not False:
        raise AdsMcpError(
            status_code=400,
            error_code="DRY_RUN_REQUIRED",
            message="Set dryRun=false to execute workflow after reviewing dry-run plan.",
            tool="execute_cross_agent_workflow",
        )

    if not request.approvalId:
        raise AdsMcpError(
            status_code=400,
            error_code="APPROVAL_REQUIRED",
            message="approvalId is required for workflow execution.",
            tool="execute_cross_agent_workflow",
        )

    services = _resolve_services(request)

    return build_success_response(
        service="orchestrator",
        tool="execute_cross_agent_workflow",
        mode="execute",
        business_key=request.businessKey,
        request_id=request_id,
        summary="Workflow accepted and queued for backend-managed execution.",
        data={
            "status": "queued",
            "services": services,
            "approvalId": request.approvalId,
            "note": "This scaffold validates execution gating and returns queue metadata. Backend worker execution is next phase.",
        },
        requires_confirmation=False,
        executed=True,
    )
