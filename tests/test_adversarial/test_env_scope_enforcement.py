"""Env-scoped API key enforcement tests.

API keys are environment-scoped (edk_{env}_{random}). These tests verify
that a staging API key cannot access production data across all mixed-auth
endpoints: SSE stream, bundles, stats, and approvals.

Risk if bypassed: Cross-environment data leak — a staging agent reads
production contracts or receives production SSE events.

GitHub issue: #34
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.auth.dependencies import AuthContext
from edictum_server.db.models import Approval, Deployment, Tenant
from edictum_server.push.manager import PushManager
from tests.conftest import TENANT_A_ID

pytestmark = pytest.mark.security

SAMPLE_YAML = """\
apiVersion: edictum/v1
kind: ContractBundle

metadata:
  name: test-bundle

contracts:
  - id: test
    type: pre
    tool: shell
    then:
      effect: deny
"""


def _make_staging_api_key() -> AuthContext:
    """API key scoped to 'staging' for tenant A."""
    return AuthContext(
        tenant_id=TENANT_A_ID,
        auth_type="api_key",
        env="staging",
        agent_id="staging-agent",
        api_key_prefix="edk_staging_test1234",
    )


def _make_production_api_key() -> AuthContext:
    """API key scoped to 'production' for tenant A."""
    return AuthContext(
        tenant_id=TENANT_A_ID,
        auth_type="api_key",
        env="production",
        agent_id="prod-agent",
        api_key_prefix="edk_production_test5678",
    )


def _make_dashboard_auth() -> AuthContext:
    """Dashboard session auth for tenant A (no env restriction)."""
    return AuthContext(
        tenant_id=TENANT_A_ID,
        auth_type="dashboard",
        user_id="user_test_123",
        email="admin@test.com",
        is_admin=True,
    )


@pytest.fixture()
def set_staging_api_key() -> Callable[[], None]:
    """Override auth to use a staging-scoped API key."""
    from edictum_server.auth.dependencies import (
        get_current_tenant,
        require_api_key,
    )

    def _swap() -> None:
        from tests.conftest import _get_app

        app = _get_app()
        app.dependency_overrides[require_api_key] = _make_staging_api_key
        app.dependency_overrides[get_current_tenant] = _make_staging_api_key

    return _swap


@pytest.fixture()
def set_production_api_key() -> Callable[[], None]:
    """Override auth to use a production-scoped API key."""
    from edictum_server.auth.dependencies import (
        get_current_tenant,
        require_api_key,
    )

    def _swap() -> None:
        from tests.conftest import _get_app

        app = _get_app()
        app.dependency_overrides[require_api_key] = _make_production_api_key
        app.dependency_overrides[get_current_tenant] = _make_production_api_key

    return _swap


@pytest.fixture()
def set_dashboard_auth() -> Callable[[], None]:
    """Override auth to use dashboard session (no env restriction)."""
    from edictum_server.auth.dependencies import (
        get_current_tenant,
        require_admin,
        require_dashboard_auth,
    )

    def _swap() -> None:
        from tests.conftest import _get_app

        app = _get_app()
        app.dependency_overrides[require_dashboard_auth] = _make_dashboard_auth
        app.dependency_overrides[require_admin] = _make_dashboard_auth
        app.dependency_overrides[get_current_tenant] = _make_dashboard_auth

    return _swap


@pytest.fixture()
async def tenant_a(db_session: AsyncSession) -> Tenant:
    """Create tenant A for the tests."""
    tenant = Tenant(id=TENANT_A_ID, name="tenant-a-env-test")
    db_session.add(tenant)
    await db_session.commit()
    return tenant


@pytest.fixture()
async def production_bundle(db_session: AsyncSession, tenant_a: Tenant) -> tuple[str, int]:
    """Upload a bundle and deploy it to production only.

    Returns (bundle_name, version).
    """
    from edictum_server.services.bundle_service import upload_bundle

    bundle = await upload_bundle(
        db=db_session,
        tenant_id=tenant_a.id,
        yaml_content=SAMPLE_YAML.encode("utf-8"),
        uploaded_by="test",
    )
    await db_session.flush()

    deployment = Deployment(
        tenant_id=tenant_a.id,
        env="production",
        bundle_name=bundle.name,
        bundle_version=bundle.version,
        deployed_by="test",
    )
    db_session.add(deployment)
    await db_session.commit()
    return bundle.name, bundle.version


@pytest.fixture()
async def production_approval(db_session: AsyncSession, tenant_a: Tenant) -> uuid.UUID:
    """Create a pending approval in the production environment.

    Returns approval_id.
    """
    approval = Approval(
        tenant_id=tenant_a.id,
        agent_id="prod-agent",
        tool_name="shell",
        message="Run command?",
        status="pending",
        env="production",
        timeout_seconds=300,
        timeout_effect="deny",
    )
    db_session.add(approval)
    await db_session.commit()
    return approval.id


# ---------------------------------------------------------------------------
# SSE stream: staging key cannot subscribe to production
# ---------------------------------------------------------------------------


async def test_sse_staging_key_cannot_subscribe_production(
    client: AsyncClient,
    set_staging_api_key: Callable[[], None],
) -> None:
    """A staging API key requesting env=production SSE stream must be rejected."""
    set_staging_api_key()
    resp = await client.get(
        "/api/v1/stream",
        params={"env": "production"},
    )
    assert resp.status_code == 403
    assert "staging" in resp.json()["detail"].lower()


async def test_sse_production_key_cannot_subscribe_staging(
    client: AsyncClient,
    set_production_api_key: Callable[[], None],
) -> None:
    """A production API key requesting env=staging SSE stream must be rejected."""
    set_production_api_key()
    resp = await client.get(
        "/api/v1/stream",
        params={"env": "staging"},
    )
    assert resp.status_code == 403
    assert "production" in resp.json()["detail"].lower()


async def test_sse_env_mismatch_detail_does_not_leak_key(
    client: AsyncClient,
    set_staging_api_key: Callable[[], None],
) -> None:
    """Error detail reveals the key's scoped env but not the key itself."""
    set_staging_api_key()
    resp = await client.get(
        "/api/v1/stream",
        params={"env": "production"},
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    # Must mention the scoped env
    assert "staging" in detail.lower()
    # Must NOT contain the API key prefix or full key
    assert "edk_staging_test1234" not in detail


# ---------------------------------------------------------------------------
# Bundle current: staging key cannot fetch production bundle
# ---------------------------------------------------------------------------


async def test_bundle_current_staging_key_denied_production(
    client: AsyncClient,
    set_staging_api_key: Callable[[], None],
    production_bundle: tuple[str, int],
) -> None:
    """Staging API key cannot access current bundle for production env."""
    set_staging_api_key()
    name, _version = production_bundle
    resp = await client.get(
        f"/api/v1/bundles/{name}/current",
        params={"env": "production"},
    )
    assert resp.status_code == 403
    assert "staging" in resp.json()["detail"].lower()


async def test_bundle_current_matching_env_allowed(
    client: AsyncClient,
    set_production_api_key: Callable[[], None],
    production_bundle: tuple[str, int],
) -> None:
    """Production API key can access current bundle for production env."""
    set_production_api_key()
    name, _version = production_bundle
    resp = await client.get(
        f"/api/v1/bundles/{name}/current",
        params={"env": "production"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bundle list: staging key only sees bundles deployed to staging
# ---------------------------------------------------------------------------


async def test_bundle_list_staging_key_excludes_production_only(
    client: AsyncClient,
    set_staging_api_key: Callable[[], None],
    production_bundle: tuple[str, int],  # noqa: ARG001 (side-effect fixture)
) -> None:
    """Staging API key should not see bundles only deployed to production."""
    set_staging_api_key()
    resp = await client.get("/api/v1/bundles")
    assert resp.status_code == 200
    bundles = resp.json()
    # No bundles should be visible since test-bundle is only deployed to production
    assert len(bundles) == 0


async def test_bundle_list_production_key_sees_production_bundles(
    client: AsyncClient,
    set_production_api_key: Callable[[], None],
    production_bundle: tuple[str, int],  # noqa: ARG001 (side-effect fixture)
) -> None:
    """Production API key should see bundles deployed to production."""
    set_production_api_key()
    resp = await client.get("/api/v1/bundles")
    assert resp.status_code == 200
    bundles = resp.json()
    assert len(bundles) == 1
    # deployed_envs should be narrowed to only production
    assert bundles[0]["deployed_envs"] == ["production"]


async def test_bundle_list_dashboard_sees_all_envs(
    client: AsyncClient,
    set_dashboard_auth: Callable[[], None],
    production_bundle: tuple[str, int],  # noqa: ARG001 (side-effect fixture)
) -> None:
    """Dashboard auth should see all bundles regardless of env."""
    set_dashboard_auth()
    resp = await client.get("/api/v1/bundles")
    assert resp.status_code == 200
    bundles = resp.json()
    assert len(bundles) == 1
    # Dashboard sees the actual deployed_envs
    assert "production" in bundles[0]["deployed_envs"]


# ---------------------------------------------------------------------------
# Bundle version: staging key cannot read production-only version
# ---------------------------------------------------------------------------


async def test_bundle_version_staging_key_denied(
    client: AsyncClient,
    set_staging_api_key: Callable[[], None],
    production_bundle: tuple[str, int],
) -> None:
    """Staging API key cannot read a bundle version deployed only to production.

    Returns 404 (not 403) to avoid leaking version existence across envs.
    """
    set_staging_api_key()
    name, version = production_bundle
    resp = await client.get(f"/api/v1/bundles/{name}/{version}")
    assert resp.status_code == 404


async def test_bundle_version_production_key_allowed(
    client: AsyncClient,
    set_production_api_key: Callable[[], None],
    production_bundle: tuple[str, int],
) -> None:
    """Production API key can read a bundle version deployed to production."""
    set_production_api_key()
    name, version = production_bundle
    resp = await client.get(f"/api/v1/bundles/{name}/{version}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bundle YAML: staging key cannot read production-only YAML
# ---------------------------------------------------------------------------


async def test_bundle_yaml_staging_key_denied(
    client: AsyncClient,
    set_staging_api_key: Callable[[], None],
    production_bundle: tuple[str, int],
) -> None:
    """Staging API key cannot read raw YAML of a bundle deployed only to production.

    Returns 404 (not 403) to avoid leaking version existence across envs.
    """
    set_staging_api_key()
    name, version = production_bundle
    resp = await client.get(f"/api/v1/bundles/{name}/{version}/yaml")
    assert resp.status_code == 404


async def test_bundle_yaml_production_key_allowed(
    client: AsyncClient,
    set_production_api_key: Callable[[], None],
    production_bundle: tuple[str, int],
) -> None:
    """Production API key can read raw YAML of a production-deployed bundle."""
    set_production_api_key()
    name, version = production_bundle
    resp = await client.get(f"/api/v1/bundles/{name}/{version}/yaml")
    assert resp.status_code == 200


async def test_bundle_yaml_dashboard_allowed(
    client: AsyncClient,
    set_dashboard_auth: Callable[[], None],
    production_bundle: tuple[str, int],
) -> None:
    """Dashboard auth can read raw YAML regardless of env."""
    set_dashboard_auth()
    name, version = production_bundle
    resp = await client.get(f"/api/v1/bundles/{name}/{version}/yaml")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Stats: staging key gets only staging-scoped stats
# ---------------------------------------------------------------------------


async def test_stats_overview_staging_key_scoped(
    client: AsyncClient,
    set_staging_api_key: Callable[[], None],
    db_session: AsyncSession,
    tenant_a: Tenant,
) -> None:
    """Stats overview with staging API key should exclude production data."""
    # Create a production approval that staging key should NOT see
    approval = Approval(
        tenant_id=tenant_a.id,
        agent_id="prod-agent",
        tool_name="shell",
        message="Run command?",
        status="pending",
        env="production",
        timeout_seconds=300,
        timeout_effect="deny",
    )
    db_session.add(approval)
    await db_session.commit()

    set_staging_api_key()
    resp = await client.get("/api/v1/stats/overview")
    assert resp.status_code == 200
    data = resp.json()
    # Staging key should see 0 pending approvals (the one we created is production)
    assert data["pending_approvals"] == 0


async def test_stats_overview_production_key_sees_production(
    client: AsyncClient,
    set_production_api_key: Callable[[], None],
    db_session: AsyncSession,
    tenant_a: Tenant,
) -> None:
    """Stats overview with production API key should include production data."""
    approval = Approval(
        tenant_id=tenant_a.id,
        agent_id="prod-agent",
        tool_name="shell",
        message="Run command?",
        status="pending",
        env="production",
        timeout_seconds=300,
        timeout_effect="deny",
    )
    db_session.add(approval)
    await db_session.commit()

    set_production_api_key()
    resp = await client.get("/api/v1/stats/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending_approvals"] == 1


async def test_stats_overview_dashboard_sees_all(
    client: AsyncClient,
    set_dashboard_auth: Callable[[], None],
    db_session: AsyncSession,
    tenant_a: Tenant,
) -> None:
    """Stats overview with dashboard auth should see all environments."""
    for env_name in ("production", "staging"):
        approval = Approval(
            tenant_id=tenant_a.id,
            agent_id=f"{env_name}-agent",
            tool_name="shell",
            message="Run command?",
            status="pending",
            env=env_name,
            timeout_seconds=300,
            timeout_effect="deny",
        )
        db_session.add(approval)
    await db_session.commit()

    set_dashboard_auth()
    resp = await client.get("/api/v1/stats/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending_approvals"] == 2


# ---------------------------------------------------------------------------
# Approval get: staging key cannot read production approval
# ---------------------------------------------------------------------------


async def test_approval_get_staging_key_denied_production(
    client: AsyncClient,
    set_staging_api_key: Callable[[], None],
    production_approval: uuid.UUID,
) -> None:
    """Staging API key cannot read a production-environment approval."""
    set_staging_api_key()
    resp = await client.get(f"/api/v1/approvals/{production_approval}")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


async def test_approval_get_production_key_allowed(
    client: AsyncClient,
    set_production_api_key: Callable[[], None],
    production_approval: uuid.UUID,
) -> None:
    """Production API key can read a production-environment approval."""
    set_production_api_key()
    resp = await client.get(f"/api/v1/approvals/{production_approval}")
    assert resp.status_code == 200
    assert resp.json()["env"] == "production"


async def test_approval_get_dashboard_sees_all(
    client: AsyncClient,
    set_dashboard_auth: Callable[[], None],
    production_approval: uuid.UUID,
) -> None:
    """Dashboard auth can read any approval regardless of env."""
    set_dashboard_auth()
    resp = await client.get(f"/api/v1/approvals/{production_approval}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PushManager unit tests: env isolation at the subscription level
# ---------------------------------------------------------------------------


async def test_push_manager_env_isolation(push_manager: PushManager) -> None:
    """PushManager must not deliver events across environments."""
    tenant = TENANT_A_ID
    prod_conn = push_manager.subscribe("production", tenant_id=tenant, agent_id="prod")
    stg_conn = push_manager.subscribe("staging", tenant_id=tenant, agent_id="stg")

    push_manager.push_to_env(
        "production",
        {"type": "contract_update", "version": 1},
        tenant_id=tenant,
    )

    assert not prod_conn.queue.empty()
    assert stg_conn.queue.empty()

    push_manager.unsubscribe("production", prod_conn)
    push_manager.unsubscribe("staging", stg_conn)
