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
from tools.write import create_ad, create_adset, create_campaign, update_adset_schedule, update_budget, update_status
from tools.publish import list_scheduled_page_videos, schedule_page_reel, schedule_page_video, upload_ad_video
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


@app.post("/tools/update_status")
def tool_update_status(request: ToolRequest, http_request: Request) -> dict:
    return update_status(request, _request_id(http_request))


@app.post("/tools/update_budget")
def tool_update_budget(request: ToolRequest, http_request: Request) -> dict:
    return update_budget(request, _request_id(http_request))


@app.post("/tools/update_adset_schedule")
def tool_update_adset_schedule(request: ToolRequest, http_request: Request) -> dict:
    return update_adset_schedule(request, _request_id(http_request))


@app.post("/tools/upload_ad_video")
def tool_upload_ad_video(request: ToolRequest, http_request: Request) -> dict:
    return upload_ad_video(request, _request_id(http_request))


@app.post("/tools/schedule_page_video")
def tool_schedule_page_video(request: ToolRequest, http_request: Request) -> dict:
    return schedule_page_video(request, _request_id(http_request))


@app.post("/tools/schedule_page_reel")
def tool_schedule_page_reel(request: ToolRequest, http_request: Request) -> dict:
    return schedule_page_reel(request, _request_id(http_request))


@app.post("/tools/list_scheduled_page_videos")
def tool_list_scheduled_page_videos(request: ToolRequest, http_request: Request) -> dict:
    return list_scheduled_page_videos(request, _request_id(http_request))


@app.post("/tools/create_campaign")
def tool_create_campaign(request: ToolRequest, http_request: Request) -> dict:
    return create_campaign(request, _request_id(http_request))


@app.post("/tools/create_adset")
def tool_create_adset(request: ToolRequest, http_request: Request) -> dict:
    return create_adset(request, _request_id(http_request))


@app.post("/tools/create_ad")
def tool_create_ad(request: ToolRequest, http_request: Request) -> dict:
    return create_ad(request, _request_id(http_request))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
