"""Evaluate (playground) endpoint — stateless contract evaluation."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from edictum_server.auth.dependencies import (
    AuthContext,
    require_dashboard_auth,
)
from edictum_server.schemas.evaluate import EvaluateRequest, EvaluateResponse
from edictum_server.services.evaluate_service import evaluate_contracts

router = APIRouter(prefix="/api/v1/bundles", tags=["evaluate"])

# Maximum time allowed for a single evaluation (seconds).
_EVALUATE_TIMEOUT_SECONDS = 5.0

# Cap concurrent evaluations so timed-out threads (which can't be killed)
# don't exhaust the default ThreadPoolExecutor.
_EVALUATE_SEMAPHORE = asyncio.Semaphore(4)


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(
    body: EvaluateRequest,
    _auth: AuthContext = Depends(require_dashboard_auth),
) -> EvaluateResponse:
    """Evaluate a tool call against YAML contracts (dashboard playground).

    This is a development-time endpoint for testing contracts in the dashboard.
    It is never called by agents during production execution.
    Evaluation runs in a thread with a timeout to prevent DoS via complex YAML.
    Concurrent evaluations are capped to prevent thread pool exhaustion.
    """
    try:
        await asyncio.wait_for(_EVALUATE_SEMAPHORE.acquire(), timeout=1.0)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent evaluations. Try again shortly.",
        ) from exc
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                evaluate_contracts,
                yaml_content=body.yaml_content,
                tool_name=body.tool_name,
                tool_args=body.tool_args,
                environment=body.environment,
                principal_input=body.principal,
            ),
            timeout=_EVALUATE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=422,
            detail="Evaluation timed out — contract bundle may be too complex.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        _EVALUATE_SEMAPHORE.release()
