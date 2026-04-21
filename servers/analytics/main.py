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
from tools.read import get_traffic_overview as _get_traffic_overview
from tools.read import get_top_pages as _get_top_pages

app = FastAPI(title="GA4 MCP", version="0.1.0")
app.add_middleware(SignedRequestMiddleware, service_name="analytics")


@app.exception_handler(AdsMcpError)
async def handle_ads_mcp_error(request: Request, exc: AdsMcpError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response(
            service="analytics",
            request_id=getattr(request.state, "request_id", None),
        ),
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "analytics", "phase": "v1"}


@app.post("/tools/get_traffic_overview")
def get_traffic_overview(request: ToolRequest, http_request: Request) -> dict:
    return _get_traffic_overview(request, request_id=getattr(http_request.state, "request_id", None))


@app.post("/tools/get_top_pages")
def get_top_pages(request: ToolRequest, http_request: Request) -> dict:
    return _get_top_pages(request, request_id=getattr(http_request.state, "request_id", None))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
