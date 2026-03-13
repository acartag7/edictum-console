"""S5: SSE channel authorization tests.

Risk if bypassed: Contract/event leak across tenants.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from edictum_server.push.manager import PushManager

pytestmark = pytest.mark.security

_TENANT = uuid.uuid4()


async def test_subscribe_without_auth(no_auth_client: AsyncClient) -> None:
    """SSE stream endpoint requires API key auth."""
    resp = await no_auth_client.get(
        "/api/v1/stream",
        params={"env": "production"},
    )
    # Should fail with 401 or 422 (missing header)
    assert resp.status_code in (401, 422)


async def test_push_only_to_subscribed_env(push_manager: PushManager) -> None:
    """Events pushed to env A are not received by env B subscribers."""
    conn_a = push_manager.subscribe(
        "production",
        tenant_id=_TENANT,
        agent_id="a1",
        bundle_name="b",
    )
    conn_b = push_manager.subscribe(
        "staging",
        tenant_id=_TENANT,
        agent_id="a2",
        bundle_name="b",
    )

    push_manager.push_to_env(
        "production",
        {"type": "contract_update", "bundle_name": "b", "version": 1},
        tenant_id=_TENANT,
    )

    # Queue A should have the event
    assert not conn_a.queue.empty()
    event = await conn_a.queue.get()
    assert event["type"] == "contract_update"

    # Queue B should be empty
    assert conn_b.queue.empty()

    push_manager.unsubscribe("production", conn_a)
    push_manager.unsubscribe("staging", conn_b)


async def test_event_type_is_contract_update(push_manager: PushManager) -> None:
    """SDK expects event type 'contract_update', not 'bundle_deployed'."""
    conn = push_manager.subscribe(
        "production",
        tenant_id=_TENANT,
        agent_id="a1",
        bundle_name="b",
    )

    push_manager.push_to_env(
        "production",
        {"type": "contract_update", "bundle_name": "b", "version": 1},
        tenant_id=_TENANT,
    )

    event = await conn.queue.get()
    assert event["type"] == "contract_update"
    assert event["type"] != "bundle_deployed"

    push_manager.unsubscribe("production", conn)


async def test_cross_environment_isolation(push_manager: PushManager) -> None:
    """Events for 'production' do not reach 'staging' subscribers."""
    prod = push_manager.subscribe(
        "production",
        tenant_id=_TENANT,
        agent_id="a1",
        bundle_name="b",
    )
    stg = push_manager.subscribe(
        "staging",
        tenant_id=_TENANT,
        agent_id="a2",
        bundle_name="b",
    )
    dev = push_manager.subscribe(
        "development",
        tenant_id=_TENANT,
        agent_id="a3",
        bundle_name="b",
    )

    push_manager.push_to_env(
        "staging",
        {"type": "contract_update", "bundle_name": "b", "env": "staging"},
        tenant_id=_TENANT,
    )

    # Only staging got it
    assert not stg.queue.empty()
    assert prod.queue.empty()
    assert dev.queue.empty()

    push_manager.unsubscribe("production", prod)
    push_manager.unsubscribe("staging", stg)
    push_manager.unsubscribe("development", dev)


async def test_unassigned_agent_does_not_receive_contract_update(
    push_manager: PushManager,
) -> None:
    """Agents with no bundle_name must NOT receive contract_update events.

    Unassigned agents receiving all bundles is a data leak — they would get
    contracts intended for other agents.
    """
    # Assigned agent should receive the event
    assigned = push_manager.subscribe(
        "production",
        tenant_id=_TENANT,
        agent_id="assigned",
        bundle_name="my-bundle",
    )
    # Unassigned agent (no bundle_name) should NOT receive it
    unassigned = push_manager.subscribe(
        "production",
        tenant_id=_TENANT,
        agent_id="unassigned",
    )

    push_manager.push_to_env(
        "production",
        {"type": "contract_update", "bundle_name": "my-bundle", "version": 1},
        tenant_id=_TENANT,
    )

    assert not assigned.queue.empty()
    assert unassigned.queue.empty(), "Unassigned agent must not receive contract_update"

    push_manager.unsubscribe("production", assigned)
    push_manager.unsubscribe("production", unassigned)
