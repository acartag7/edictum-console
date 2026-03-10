"""S4: Approval state transition tests.

Risk if bypassed: Unauthorized tool execution.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.db.models import Approval

pytestmark = pytest.mark.security


async def _create_approval(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/v1/approvals",
        json={
            "agent_id": "agent-1",
            "tool_name": "shell",
            "tool_args": {"cmd": "rm -rf /"},
            "message": "Agent wants to run a dangerous command",
            "timeout": 300,
            "timeout_effect": "deny",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def test_double_approve(client: AsyncClient) -> None:
    """Approving the same approval twice -> second returns 409."""
    created = await _create_approval(client)
    first = await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": True, "decided_by": "admin"},
    )
    assert first.status_code == 200

    second = await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": True, "decided_by": "admin"},
    )
    assert second.status_code == 409


async def test_approve_then_deny(client: AsyncClient) -> None:
    """Approving then denying the same approval -> second returns 409."""
    created = await _create_approval(client)
    await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": True, "decided_by": "admin"},
    )
    resp = await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": False, "decided_by": "admin"},
    )
    assert resp.status_code == 409


async def test_deny_then_approve(client: AsyncClient) -> None:
    """Denying then approving the same approval -> second returns 409."""
    created = await _create_approval(client)
    await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": False, "decided_by": "admin"},
    )
    resp = await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": True, "decided_by": "admin"},
    )
    assert resp.status_code == 409


async def test_approve_nonexistent(client: AsyncClient) -> None:
    """Approving a non-existent approval -> 409 (not found or already decided)."""
    fake_id = str(uuid.uuid4())
    resp = await client.put(
        f"/api/v1/approvals/{fake_id}",
        json={"approved": True, "decided_by": "admin"},
    )
    assert resp.status_code == 409


async def test_approve_wrong_tenant(
    client: AsyncClient,
    set_auth_tenant_b: Callable[[], None],
) -> None:
    """Approving tenant A's approval as tenant B -> 409 (not found)."""
    created = await _create_approval(client)
    set_auth_tenant_b()
    resp = await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": True, "decided_by": "attacker"},
    )
    assert resp.status_code == 409


async def test_approve_expired_approval_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Approving a logically-expired approval must return 409.

    Attack: approval has timeout=300s but 310s have passed. The timeout
    worker hasn't run yet, so status is still 'pending'. An attacker
    clicks "Approve" — the _ApprovalNotExpired guard in submit_decision
    must reject it.
    """
    created = await _create_approval(client)
    approval_id = uuid.UUID(created["id"])

    # Backdate created_at so the approval is logically expired
    await db_session.execute(
        update(Approval)
        .where(Approval.id == approval_id)
        .values(created_at=datetime.now(UTC) - timedelta(seconds=600))
    )
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": True},
    )
    assert resp.status_code == 409


async def test_deny_expired_approval_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Denying a logically-expired approval must also return 409."""
    created = await _create_approval(client)
    approval_id = uuid.UUID(created["id"])

    await db_session.execute(
        update(Approval)
        .where(Approval.id == approval_id)
        .values(created_at=datetime.now(UTC) - timedelta(seconds=600))
    )
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": False},
    )
    assert resp.status_code == 409


async def test_approve_just_before_expiry_succeeds(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An approval still within its timeout window must be approvable."""
    created = await _create_approval(client)
    approval_id = uuid.UUID(created["id"])

    # Backdate to 290s ago (timeout is 300s — still 10s remaining)
    await db_session.execute(
        update(Approval)
        .where(Approval.id == approval_id)
        .values(created_at=datetime.now(UTC) - timedelta(seconds=290))
    )
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/approvals/{created['id']}",
        json={"approved": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
