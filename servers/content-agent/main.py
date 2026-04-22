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
from tools.write import write_google_ad as _write_google_ad, write_review_reply as _write_review_reply

app = FastAPI(title="Content Agent MCP", version="0.1.0")
app.add_middleware(SignedRequestMiddleware, service_name="content")


class ContentRequest(BaseModel):
    businessKey: str
    contentType: str | None = None
    payload: dict | None = None
    # Legacy flat fields (still accepted for backwards compat)
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


@app.exception_handler(RuntimeError)
async def handle_runtime_error(request: Request, exc: RuntimeError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "service": "content",
            "tool": "write_google_ad",
            "errorCode": "SERVICE_UNAVAILABLE",
            "message": str(exc),
        },
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "content", "phase": "live"}


@app.post("/tools/write_google_ad")
def write_google_ad(request: ContentRequest, http_request: Request) -> dict:
    request_id = getattr(http_request.state, "request_id", None)

    # Merge payload dict with any flat legacy fields
    payload = dict(request.payload or {})
    if request.keyword and "keyword" not in payload:
        payload["keyword"] = request.keyword
    if request.city and "city" not in payload:
        payload["city"] = request.city
    if request.topic and "campaignGoal" not in payload:
        payload["campaignGoal"] = request.topic

    result = _write_google_ad(business_key=request.businessKey, payload=payload)

    headline_count = len(result["headlines"])
    desc_count = len(result["descriptions"])
    summary = (
        f"Generated {headline_count} headlines and {desc_count} descriptions "
        f"for '{request.businessKey}' using {result['model']}."
    )

    return build_success_response(
        service="content",
        tool="write_google_ad",
        mode="draft",
        business_key=request.businessKey,
        request_id=request_id,
        summary=summary,
        data={
            "contentType": request.contentType or "google_ad_rsa",
            "headlines": result["headlines"],
            "descriptions": result["descriptions"],
            "warnings": result["warnings"],
            "model": result["model"],
            "usage": {
                "inputTokens": result["inputTokens"],
                "outputTokens": result["outputTokens"],
            },
        },
        requires_confirmation=False,
        executed=False,
    )


class ReviewReplyRequest(BaseModel):
    businessKey: str
    reviewerName: str | None = None
    starRating: str | int = "FIVE"
    reviewText: str | None = None
    existingReply: str | None = None
    tone: str | None = None


@app.post("/tools/write_review_reply")
def write_review_reply(request: ReviewReplyRequest, http_request: Request) -> dict:
    request_id = getattr(http_request.state, "request_id", None)

    payload = {
        "reviewerName": request.reviewerName,
        "starRating": request.starRating,
        "reviewText": request.reviewText,
        "existingReply": request.existingReply,
        "tone": request.tone or "professional and friendly",
    }

    result = _write_review_reply(business_key=request.businessKey, payload=payload)

    return build_success_response(
        service="content",
        tool="write_review_reply",
        mode="draft",
        business_key=request.businessKey,
        request_id=request_id,
        summary=f"Generated review reply for '{request.businessKey}' using {result['model']}.",
        data={
            "reply": result["reply"],
            "model": result["model"],
            "usage": {
                "inputTokens": result["inputTokens"],
                "outputTokens": result["outputTokens"],
            },
        },
        requires_confirmation=True,
        executed=False,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8005)
