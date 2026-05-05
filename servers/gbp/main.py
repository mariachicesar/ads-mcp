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

# Import GBP tool handlers (lazy to avoid import errors if deps not installed)
def _gbp_handler(tool_name, fn):
    def handler(request: ToolRequest, http_request: Request) -> dict:
        try:
            return fn(request, request_id=getattr(http_request.state, "request_id", None))
        except AdsMcpError as exc:
            raise
        except Exception as exc:
            raise AdsMcpError(
                status_code=500,
                error_code="INTERNAL_ERROR",
                message=str(exc),
                tool=tool_name,
            ) from exc
    handler.__name__ = tool_name
    return handler

app = FastAPI(title="GBP MCP", version="0.1.0")
app.add_middleware(SignedRequestMiddleware, service_name="gbp")


@app.exception_handler(AdsMcpError)
async def handle_ads_mcp_error(request: Request, exc: AdsMcpError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response(
            service="gbp",
            request_id=getattr(request.state, "request_id", None),
        ),
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "gbp", "phase": "v1"}


# ── Read tools ──────────────────────────────────────────────────────────────

@app.post("/tools/get_location_info")
def get_location_info(request: ToolRequest, http_request: Request) -> dict:
    from servers.gbp.gbp_client import fetch_location_info
    from shared.runtime_config import load_platform_runtime_config
    from shared.responses import build_success_response
    config = load_platform_runtime_config(
        platform="gbp", business_key=request.businessKey,
        required_keys=("client_id", "client_secret", "refresh_token", "gbp_location_id"),
        tool="get_location_info",
    )
    data = fetch_location_info(config, tool="get_location_info")
    return build_success_response(
        service="gbp", tool="get_location_info", mode="read",
        business_key=request.businessKey,
        request_id=getattr(http_request.state, "request_id", None),
        summary=f"Location info for {request.businessKey}", data=data,
    )


@app.post("/tools/list_reviews")
def list_reviews(request: ToolRequest, http_request: Request) -> dict:
    from servers.gbp.gbp_client import list_reviews as _list_reviews
    from shared.runtime_config import load_platform_runtime_config
    from shared.responses import build_success_response
    config = load_platform_runtime_config(
        platform="gbp", business_key=request.businessKey,
        required_keys=("client_id", "client_secret", "refresh_token", "gbp_location_id"),
        tool="list_reviews",
    )
    filter_reply = request.payload.get("filterReply")
    page_size = min(int(request.payload.get("pageSize", 20)), 50)
    data = _list_reviews(config, page_size=page_size, filter_reply=filter_reply, tool="list_reviews")
    return build_success_response(
        service="gbp", tool="list_reviews", mode="read",
        business_key=request.businessKey,
        request_id=getattr(http_request.state, "request_id", None),
        summary=f"{data['totalReviews']} reviews for {request.businessKey}", data=data,
    )


@app.post("/tools/list_posts")
def list_posts(request: ToolRequest, http_request: Request) -> dict:
    from servers.gbp.gbp_client import list_posts as _list_posts
    from shared.runtime_config import load_platform_runtime_config
    from shared.responses import build_success_response
    config = load_platform_runtime_config(
        platform="gbp", business_key=request.businessKey,
        required_keys=("client_id", "client_secret", "refresh_token", "gbp_location_id"),
        tool="list_posts",
    )
    page_size = min(int(request.payload.get("pageSize", 10)), 20)
    data = _list_posts(config, page_size=page_size, tool="list_posts")
    return build_success_response(
        service="gbp", tool="list_posts", mode="read",
        business_key=request.businessKey,
        request_id=getattr(http_request.state, "request_id", None),
        summary=f"{data['totalPosts']} posts for {request.businessKey}", data=data,
    )


@app.post("/tools/list_media")
def list_media(request: ToolRequest, http_request: Request) -> dict:
    from servers.gbp.gbp_client import list_media as _list_media
    from shared.runtime_config import load_platform_runtime_config
    from shared.responses import build_success_response
    config = load_platform_runtime_config(
        platform="gbp", business_key=request.businessKey,
        required_keys=("client_id", "client_secret", "refresh_token", "gbp_location_id"),
        tool="list_media",
    )
    page_size = min(int(request.payload.get("pageSize", 20)), 50)
    data = _list_media(config, page_size=page_size, tool="list_media")
    return build_success_response(
        service="gbp", tool="list_media", mode="read",
        business_key=request.businessKey,
        request_id=getattr(http_request.state, "request_id", None),
        summary=f"{data['totalItems']} media items for {request.businessKey}", data=data,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
