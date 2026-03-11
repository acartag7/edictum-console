"""Schemas for the approval-queue endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ApprovalStatusType = Literal["pending", "approved", "denied", "timeout"]


class CreateApprovalRequest(BaseModel):
    agent_id: str = Field(..., max_length=255)
    tool_name: str = Field(..., max_length=255)
    tool_args: dict[str, Any] | None = None
    message: str = Field(..., max_length=10_000)
    timeout: int = Field(default=300, ge=1, le=86400)
    timeout_effect: Literal["deny", "allow"] = "deny"
    decision_source: str | None = Field(default=None, max_length=255)
    contract_name: str | None = Field(default=None, max_length=255)


class ApprovalResponse(BaseModel):
    id: str
    status: str
    agent_id: str
    tool_name: str
    tool_args: dict[str, Any] | None = None
    message: str
    env: str
    timeout_seconds: int
    timeout_effect: str
    decision_source: str | None = None
    contract_name: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    decided_via: str | None = None
    created_at: datetime


class SubmitDecisionRequest(BaseModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=2000)
    decided_via: str | None = Field(default=None, max_length=64)
