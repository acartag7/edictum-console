"""Schemas for the session-state endpoints."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


class SessionValueResponse(BaseModel):
    """Response when reading a session key."""

    value: str | None


class SetValueRequest(BaseModel):
    """Request body for setting a session key."""

    value: str = Field(..., max_length=1_048_576, description="The string value to store")


class BatchGetRequest(BaseModel):
    """Request body for batch-reading multiple session keys."""

    keys: list[
        Annotated[str, StringConstraints(max_length=256, pattern=r"^[a-zA-Z0-9_\-\.:/]+$")]
    ] = Field(..., max_length=100, description="Keys to retrieve")


class BatchGetResponse(BaseModel):
    """Response for batch-reading multiple session keys."""

    values: dict[str, str | None]


class IncrementRequest(BaseModel):
    """Request body for incrementing a numeric session key."""

    amount: float = Field(default=1, description="Amount to increment by")


class IncrementResponse(BaseModel):
    """Response after incrementing a session key."""

    value: float
