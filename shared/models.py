from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


KNOWN_PLATFORMS: tuple[str, ...] = (
    "google-ads",
    "analytics",
    "search-console",
    "gbp",
    "meta-ads",
)


class PlatformConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False

    def metadata(self) -> dict[str, Any]:
        dumped = self.model_dump()
        dumped.pop("enabled", None)
        return {k: v for k, v in dumped.items() if v is not None}


class ClientManifest(BaseModel):
    businessKey: str
    displayName: str
    tenantId: int | None = None
    industry: str | None = None
    status: Literal["active", "paused", "archived"] = "active"
    platforms: dict[str, PlatformConfig] = Field(default_factory=dict)
    createdAt: str | None = None
    updatedAt: str | None = None

    @model_validator(mode="after")
    def _fill_platforms(self) -> "ClientManifest":
        for key in KNOWN_PLATFORMS:
            self.platforms.setdefault(key, PlatformConfig(enabled=False))
        return self

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump()


class PlatformReadiness(BaseModel):
    enabled: bool
    ready: bool
    missingKeys: list[str] = Field(default_factory=list)


class ReadinessReport(BaseModel):
    businessKey: str
    displayName: str
    status: str
    platforms: dict[str, PlatformReadiness]
