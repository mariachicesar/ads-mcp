from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolRequest(BaseModel):
    tenantId: int | None = None
    businessKey: str
    requestedBy: int | str | None = None
    approvalId: str | None = None
    dryRun: bool | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ruleContext: dict[str, Any] = Field(default_factory=dict)
    requestMeta: dict[str, Any] = Field(default_factory=dict)


class RuleCheck(BaseModel):
    rule: str
    passed: bool
    message: str
    severity: str = "info"
    source: str = "CLAUDE.md"


class Change(BaseModel):
    field: str
    label: str
    before: Any = None
    after: Any = None
    status: str
