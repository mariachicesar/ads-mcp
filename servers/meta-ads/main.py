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
from shared.responses import build_success_response

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


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "meta-ads", "phase": "foundation"}


@app.post("/tools/get_campaign_performance")
def get_campaign_performance(request: ToolRequest, http_request: Request) -> dict:
    return build_success_response(
        service="meta-ads",
        tool="get_campaign_performance",
        mode="read",
        business_key=request.businessKey,
        request_id=getattr(http_request.state, "request_id", None),
        summary="Placeholder Meta Ads performance response.",
        data={"rows": [], "payload": request.payload},
        freshness={"state": "live"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
