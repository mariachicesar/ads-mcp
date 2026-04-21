from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tools.read import list_accounts, get_campaign_performance
from tools.write import update_campaign_budget
from shared.auth import SignedRequestMiddleware
from shared.errors import AdsMcpError
from shared.models import ToolRequest

app = FastAPI(title="Google Ads MCP", version="0.1.0")
app.add_middleware(SignedRequestMiddleware, service_name="google-ads")


@app.exception_handler(AdsMcpError)
async def handle_ads_mcp_error(request: Request, exc: AdsMcpError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response(
            service="google-ads",
            request_id=getattr(request.state, "request_id", None),
        ),
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "google-ads", "phase": "foundation"}


@app.post("/tools/list_accounts")
def tool_list_accounts(request: ToolRequest, http_request: Request) -> dict:
    return list_accounts(request, getattr(http_request.state, "request_id", None))


@app.post("/tools/get_campaign_performance")
def tool_get_campaign_performance(request: ToolRequest, http_request: Request) -> dict:
    return get_campaign_performance(request, getattr(http_request.state, "request_id", None))


@app.post("/tools/update_campaign_budget")
def tool_update_campaign_budget(request: ToolRequest, http_request: Request) -> dict:
    return update_campaign_budget(request, getattr(http_request.state, "request_id", None))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
