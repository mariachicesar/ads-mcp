from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shared.auth import SignedRequestMiddleware
from shared.errors import AdsMcpError
from shared.responses import build_success_response

BRANDS_DIR = Path(__file__).resolve().parent / "brands"

app = FastAPI(title="Content Agent MCP", version="0.1.0")
app.add_middleware(SignedRequestMiddleware, service_name="content")


class ContentRequest(BaseModel):
    businessKey: str
    contentType: str
    topic: str | None = None
    keyword: str | None = None
    city: str | None = None


@app.exception_handler(AdsMcpError)
async def handle_ads_mcp_error(request: Request, exc: AdsMcpError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response(
            service="content",
            request_id=getattr(request.state, "request_id", None),
        ),
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "content", "phase": "foundation"}


@app.post("/tools/write_google_ad")
def write_google_ad(request: ContentRequest, http_request: Request) -> dict:
    brand_file = BRANDS_DIR / f"{request.businessKey}.md"
    brand_context = brand_file.read_text(encoding="utf-8") if brand_file.exists() else ""
    return build_success_response(
        service="content",
        tool="write_google_ad",
        mode="draft",
        business_key=request.businessKey,
        request_id=getattr(http_request.state, "request_id", None),
        summary="Placeholder content generation response.",
        data={
            "contentType": request.contentType,
            "topic": request.topic,
            "keyword": request.keyword,
            "city": request.city,
            "brandContextLoaded": bool(brand_context),
            "draft": {
                "headlines": [],
                "descriptions": [],
            },
        },
        requires_confirmation=False,
        executed=False,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8005)
