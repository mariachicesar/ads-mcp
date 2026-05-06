"""Orchestrator MCP server - FastMCP protocol layer.

Exposes cross-agent workflow planning and execution gating tools.

Run via stdio:
    python servers/orchestrator/mcp_server.py
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tools.workflow import plan_cross_agent_workflow, execute_cross_agent_workflow

mcp = FastMCP(
    name="orchestrator",
    instructions=(
        "Cross-agent workflow planning tools for ads-mcp. "
        "Always run plan_cross_agent_workflow first, review steps, and only then "
        "execute with dry_run=false and explicit approval_id."
    ),
)


def _error_response(exc: Exception, tool: str) -> dict:
    if isinstance(exc, AdsMcpError):
        return exc.to_response(service="orchestrator", request_id=None)
    return AdsMcpError(
        status_code=500,
        error_code="INTERNAL_ERROR",
        message="Unhandled orchestrator tool error.",
        tool=tool,
        details={"reason": str(exc)},
    ).to_response(service="orchestrator", request_id=None)


@mcp.tool()
def orchestrator_plan_cross_agent_workflow(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    objective: Annotated[str, Field(description="High-level orchestration objective")] = "Cross-agent optimization workflow",
    services: Annotated[list[str] | None, Field(description="Optional subset of services to include")]=None,
    mcp_base_url: Annotated[str, Field(description="Base URL for routed service endpoints")] = "https://mcp.rctechbridge.com",
) -> dict:
    """Generate a dry-run cross-agent workflow plan with ordered service steps."""
    request = ToolRequest(
        businessKey=business_key,
        dryRun=True,
        payload={"objective": objective, "services": services or []},
        requestMeta={"mcpBaseUrl": mcp_base_url},
    )
    try:
        return plan_cross_agent_workflow(request, request_id=None)
    except Exception as exc:
        return _error_response(exc, "orchestrator_plan_cross_agent_workflow")


@mcp.tool()
def orchestrator_execute_cross_agent_workflow(
    business_key: Annotated[str, Field(description="Business key, e.g. 'rnr-electrician' or 'gq-painting'")],
    approval_id: Annotated[str, Field(description="Approval ID from dry-run response")],
    objective: Annotated[str, Field(description="High-level orchestration objective")] = "Cross-agent optimization workflow",
    services: Annotated[list[str] | None, Field(description="Optional subset of services to include")]=None,
) -> dict:
    """Queue a validated workflow execution request after explicit approval."""
    request = ToolRequest(
        businessKey=business_key,
        dryRun=False,
        approvalId=approval_id,
        payload={"objective": objective, "services": services or []},
    )
    try:
        return execute_cross_agent_workflow(request, request_id=None)
    except Exception as exc:
        return _error_response(exc, "orchestrator_execute_cross_agent_workflow")


if __name__ == "__main__":
    mcp.run()
