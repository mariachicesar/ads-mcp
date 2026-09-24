from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tools.read import (
    get_ad_performance,
    get_adset_performance,
    get_audience_performance,
    get_campaign_performance,
    get_publisher_platform_breakdown,
    list_ad_accounts,
    list_creative_assets,
)
from shared.auth import SignedRequestMiddleware
from shared.errors import AdsMcpError
from shared.models import ToolRequest

app = FastAPI(title="Meta Ads MCP", version="0.1.0")
app.add_middleware(SignedRequestMiddleware, service_name="meta-ads")


@app.exception_handler(AdsMcpError)
async def handle_ads_mcp_error(request: Request, exc: AdsMcpError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response(
            service="meta-ads",
            request_id=getattr(request.state, "request_id", None),
        ),
    )


@app.exception_handler(Exception)
async def handle_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "service": "meta-ads",
            "errorCode": "INTERNAL_ERROR",
            "message": str(exc) or "An unexpected error occurred.",
            "requestId": getattr(request.state, "request_id", None),
        },
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "meta-ads", "phase": "foundation"}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@app.post("/tools/list_ad_accounts")
def tool_list_ad_accounts(request: ToolRequest, http_request: Request) -> dict:
    return list_ad_accounts(request, _request_id(http_request))


@app.post("/tools/get_campaign_performance")
def tool_get_campaign_performance(request: ToolRequest, http_request: Request) -> dict:
    return get_campaign_performance(request, _request_id(http_request))


@app.post("/tools/get_adset_performance")
def tool_get_adset_performance(request: ToolRequest, http_request: Request) -> dict:
    return get_adset_performance(request, _request_id(http_request))


@app.post("/tools/get_ad_performance")
def tool_get_ad_performance(request: ToolRequest, http_request: Request) -> dict:
    return get_ad_performance(request, _request_id(http_request))


@app.post("/tools/get_publisher_platform_breakdown")
def tool_get_publisher_platform_breakdown(request: ToolRequest, http_request: Request) -> dict:
    return get_publisher_platform_breakdown(request, _request_id(http_request))


@app.post("/tools/get_audience_performance")
def tool_get_audience_performance(request: ToolRequest, http_request: Request) -> dict:
    return get_audience_performance(request, _request_id(http_request))


@app.post("/tools/list_creative_assets")
def tool_list_creative_assets(request: ToolRequest, http_request: Request) -> dict:
    return list_creative_assets(request, _request_id(http_request))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
