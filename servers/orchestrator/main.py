from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared.auth import SignedRequestMiddleware
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tools.workflow import plan_cross_agent_workflow, execute_cross_agent_workflow

app = FastAPI(title="Orchestrator MCP", version="0.1.0")
app.add_middleware(SignedRequestMiddleware, service_name="orchestrator")


@app.exception_handler(AdsMcpError)
async def handle_ads_mcp_error(request: Request, exc: AdsMcpError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response(
            service="orchestrator",
            request_id=getattr(request.state, "request_id", None),
        ),
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "orchestrator", "phase": "v0-scaffold"}


@app.post("/tools/plan_cross_agent_workflow")
def tool_plan_cross_agent_workflow(request: ToolRequest, http_request: Request) -> dict:
    return plan_cross_agent_workflow(request, getattr(http_request.state, "request_id", None))


@app.post("/tools/execute_cross_agent_workflow")
def tool_execute_cross_agent_workflow(request: ToolRequest, http_request: Request) -> dict:
    return execute_cross_agent_workflow(request, getattr(http_request.state, "request_id", None))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8007)
