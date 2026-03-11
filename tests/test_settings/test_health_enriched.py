"""Tests for authenticated health details endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_health_details_returns_enriched_fields(client: AsyncClient) -> None:
    """Authenticated /health/details returns database, redis, and connected_agents."""
    resp = await client.get("/api/v1/health/details")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] in ("ok", "degraded")
    assert "database" in data
    assert data["database"]["connected"] is True
    assert isinstance(data["database"]["latency_ms"], (int, float))

    assert "redis" in data
    assert data["redis"]["connected"] is True
    assert isinstance(data["redis"]["latency_ms"], (int, float))

    assert "connected_agents" in data
    assert isinstance(data["connected_agents"], int)
    assert data["connected_agents"] == 0


async def test_public_health_hides_enriched_fields(client: AsyncClient) -> None:
    """Public /health does not leak database/redis/agent details."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "database" not in data
    assert "redis" not in data
    assert "connected_agents" not in data
