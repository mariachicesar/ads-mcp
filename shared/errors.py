"""Structured error type for ads-mcp services.

Known error codes include: REQUEST_INVALID, AUTH_INVALID, AUTH_EXPIRED,
INTERNAL_ERROR, PLATFORM_NOT_CONFIGURED (404 — client has no manifest entry
for the platform, or it is disabled), PLATFORM_CONFIG_INCOMPLETE (409 —
platform enabled but credential secret is missing required keys).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdsMcpError(Exception):
    status_code: int
    error_code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    rule_checks: list[dict[str, Any]] = field(default_factory=list)
    tool: str | None = None

    def __str__(self) -> str:
        return self.message

    def to_response(self, *, service: str, request_id: str | None) -> dict[str, Any]:
        response = {
            "ok": False,
            "service": service,
            "tool": self.tool,
            "requestId": request_id,
            "errorCode": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            response["details"] = self.details
        if self.rule_checks:
            response["ruleChecks"] = self.rule_checks
        return response
