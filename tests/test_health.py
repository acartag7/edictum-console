"""Tests for the health endpoints (public + authenticated details)."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_public_minimal(client: AsyncClient) -> None:
    """Public /health returns only status + bootstrap_complete."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "bootstrap_complete" in data
    # Must NOT include sensitive operational details
    assert "version" not in data
    assert "auth_provider" not in data
    assert "database" not in data
    assert "redis" not in data
    assert "connected_agents" not in data
    assert "workers" not in data


async def test_health_bootstrap_false_when_no_users(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    data = resp.json()
    assert data["bootstrap_complete"] is False


async def test_health_details_requires_auth(no_auth_client: AsyncClient) -> None:
    """/health/details returns 401 without a session cookie."""
    resp = await no_auth_client.get("/api/v1/health/details")
    assert resp.status_code == 401


async def test_health_details_returns_full_info(client: AsyncClient) -> None:
    """/health/details returns version, db, redis, etc. for authenticated users."""
    resp = await client.get("/api/v1/health/details")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert "version" in data
    assert isinstance(data["version"], str)
    assert data["auth_provider"] == "local"
    assert "bootstrap_complete" in data
    assert "database" in data
    assert "redis" in data
