"""Unit tests for PushManager with AgentConnection metadata."""

from __future__ import annotations

import uuid

import pytest

from edictum_server.push.manager import AgentConnection, PushManager

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


@pytest.fixture()
def push() -> PushManager:
    return PushManager()


def test_subscribe_returns_agent_connection(push: PushManager) -> None:
    conn = push.subscribe(
        "production",
        tenant_id=TENANT_A,
        agent_id="agent-1",
        bundle_name="devops-agent",
        policy_version="abc123",
    )
    assert isinstance(conn, AgentConnection)
    assert conn.env == "production"
    assert conn.tenant_id == TENANT_A
    assert conn.agent_id == "agent-1"
    assert conn.bundle_name == "devops-agent"
    assert conn.policy_version == "abc123"
    assert conn.connected_at is not None


def test_push_to_env_filters_by_tenant(push: PushManager) -> None:
    conn_a = push.subscribe(
        "production",
        tenant_id=TENANT_A,
        agent_id="a1",
        bundle_name="b",
    )
    conn_b = push.subscribe(
        "production",
        tenant_id=TENANT_B,
        agent_id="b1",
        bundle_name="b",
    )

    push.push_to_env(
        "production",
        {"type": "contract_update", "bundle_name": "b", "v": 1},
        tenant_id=TENANT_A,
    )

    assert not conn_a.queue.empty()
    assert conn_b.queue.empty()


def test_push_to_env_filters_by_bundle_name(push: PushManager) -> None:
    conn_devops = push.subscribe(
        "production", tenant_id=TENANT_A, agent_id="a1", bundle_name="devops-agent"
    )
    conn_research = push.subscribe(
        "production", tenant_id=TENANT_A, agent_id="a2", bundle_name="research-agent"
    )

    push.push_to_env(
        "production",
        {"type": "contract_update", "bundle_name": "devops-agent", "v": 1},
        tenant_id=TENANT_A,
    )

    assert not conn_devops.queue.empty()
    assert conn_research.queue.empty()


def test_push_to_env_unassigned_receives_nothing(push: PushManager) -> None:
    """Connection with no bundle_name must NOT receive contract_update events.

    Unassigned agents getting all bundles is a data leak — they'd receive
    contracts intended for other agents.
    """
    conn_unassigned = push.subscribe("production", tenant_id=TENANT_A, agent_id="a1")
    conn_assigned = push.subscribe(
        "production", tenant_id=TENANT_A, agent_id="a2", bundle_name="devops-agent"
    )

    push.push_to_env(
        "production",
        {"type": "contract_update", "bundle_name": "devops-agent", "v": 1},
        tenant_id=TENANT_A,
    )

    # Unassigned connection must NOT receive anything
    assert conn_unassigned.queue.empty()
    # Assigned connection for matching bundle receives it
    assert not conn_assigned.queue.empty()


def test_push_to_env_non_contract_update_ignores_bundle_filter(push: PushManager) -> None:
    """Non-contract_update events are delivered regardless of bundle_name filter."""
    conn = push.subscribe(
        "production", tenant_id=TENANT_A, agent_id="a1", bundle_name="devops-agent"
    )

    push.push_to_env(
        "production",
        {"type": "approval_timeout", "agent_id": "a1"},
        tenant_id=TENANT_A,
    )

    assert not conn.queue.empty()


def test_get_agent_connections_by_tenant(push: PushManager) -> None:
    push.subscribe("production", tenant_id=TENANT_A, agent_id="a1")
    push.subscribe("staging", tenant_id=TENANT_A, agent_id="a2")
    push.subscribe("production", tenant_id=TENANT_B, agent_id="b1")

    conns = push.get_agent_connections(TENANT_A)
    assert len(conns) == 2
    assert all(c.tenant_id == TENANT_A for c in conns)


def test_get_agent_connections_by_bundle_name(push: PushManager) -> None:
    push.subscribe("production", tenant_id=TENANT_A, agent_id="a1", bundle_name="devops-agent")
    push.subscribe("production", tenant_id=TENANT_A, agent_id="a2", bundle_name="research-agent")

    conns = push.get_agent_connections(TENANT_A, bundle_name="devops-agent")
    assert len(conns) == 1
    assert conns[0].agent_id == "a1"


def test_unsubscribe_removes_connection(push: PushManager) -> None:
    conn = push.subscribe("production", tenant_id=TENANT_A, agent_id="a1")
    assert push.connection_count == 1

    push.unsubscribe("production", conn)
    assert push.connection_count == 0
    assert push.get_agent_connections(TENANT_A) == []
