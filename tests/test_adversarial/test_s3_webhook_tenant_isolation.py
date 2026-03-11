"""S3: Webhook handler tenant isolation tests.

Covers the cross-tenant attack where a webhook callback for tenant A's channel
carries a Redis-resolved tenant_id that belongs to tenant B. Without the
cross-check, submit_decision would run against tenant B's approval using
tenant B's tenant_id, allowing cross-tenant approval manipulation.

Risk if bypassed: Unauthorised approval/denial of another tenant's tool call.
SHIP-BLOCKER (S3).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from edictum_server.db.models import Approval, NotificationChannel
from edictum_server.services.notification_service import encrypt_config
from tests.conftest import TENANT_A_ID, TENANT_B_ID, _test_session_factory

pytestmark = pytest.mark.security

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIGNING_KEY_SECRET = bytes.fromhex("0" * 64)  # matches conftest env default


def _make_slack_channel(tenant_id: uuid.UUID, signing_secret: str) -> NotificationChannel:
    config = {"signing_secret": signing_secret, "bot_token": "xoxb-fake", "slack_channel": "#test"}
    ch = NotificationChannel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="test-slack",
        channel_type="slack_app",
        enabled=True,
    )
    ch.config_encrypted = encrypt_config(config, _SIGNING_KEY_SECRET)
    ch.config = None
    return ch


def _make_discord_channel(tenant_id: uuid.UUID, public_key_hex: str) -> NotificationChannel:
    config = {
        "public_key": public_key_hex,
        "bot_token": "fake-token",
        "discord_channel_id": "123456789",
    }
    ch = NotificationChannel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="test-discord",
        channel_type="discord",
        enabled=True,
    )
    ch.config_encrypted = encrypt_config(config, _SIGNING_KEY_SECRET)
    ch.config = None
    return ch


def _make_telegram_channel(tenant_id: uuid.UUID, webhook_secret: str) -> NotificationChannel:
    config = {
        "bot_token": "fake-token",
        "chat_id": 12345,
        "webhook_secret": webhook_secret,
    }
    ch = NotificationChannel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="test-telegram",
        channel_type="telegram",
        enabled=True,
    )
    ch.config_encrypted = encrypt_config(config, _SIGNING_KEY_SECRET)
    ch.config = None
    return ch


def _make_approval(tenant_id: uuid.UUID) -> Approval:
    return Approval(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id="agent-1",
        tool_name="shell",
        message="test",
        status="pending",
        env="production",
        timeout_seconds=300,
        timeout_effect="deny",
    )


def _slack_signature(signing_secret: str, timestamp: str, body: bytes) -> str:
    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    return (
        "v0="
        + hmac.new(signing_secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
    )


# ---------------------------------------------------------------------------
# Slack: Redis tenant_id belongs to tenant B, channel belongs to tenant A
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_cross_tenant_redis_mismatch_rejected(
    test_redis: Any,
    push_manager: Any,
) -> None:
    """Slack webhook: Redis-resolved tenant_id (B) != channel tenant_id (A) -> 403.

    Attack: Approval was created for tenant B. An attacker or tampered Redis
    key routes the approval decision through tenant A's Slack channel. Without
    the S3 cross-check, submit_decision would run with tenant B's tenant_id,
    allowing cross-tenant approval manipulation.
    """
    signing_secret = "test-slack-signing-secret-for-channel-a"

    # Insert tenant A's channel into DB
    async with _test_session_factory() as db:
        channel_a = _make_slack_channel(TENANT_A_ID, signing_secret)
        db.add(channel_a)
        # Create an approval belonging to tenant B
        approval_b = _make_approval(TENANT_B_ID)
        db.add(approval_b)
        await db.commit()
        channel_a_id = str(channel_a.id)
        approval_b_id = approval_b.id

    # Seed Redis with tenant B's tenant_id stored under channel A's key
    # This simulates a cross-tenant Redis key (tampered or mis-routed)
    await test_redis.set(
        f"slack:tenant:{channel_a_id}:{approval_b_id}",
        str(TENANT_B_ID),
        ex=3600,
    )

    # Build a valid Slack interaction payload for tenant A's channel
    action_id = f"edictum_approve:{approval_b_id}"
    slack_payload = {
        "type": "block_actions",
        "actions": [{"action_id": action_id}],
        "user": {"username": "attacker"},
    }
    body_str = urllib.parse.urlencode({"payload": json.dumps(slack_payload)})
    body_bytes = body_str.encode()
    timestamp = str(int(time.time()))
    signature = _slack_signature(signing_secret, timestamp, body_bytes)

    from edictum_server.db.engine import get_db
    from edictum_server.main import app
    from edictum_server.push.manager import get_push_manager
    from edictum_server.redis.client import get_redis
    from tests.conftest import _override_get_db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = lambda: test_redis
    app.dependency_overrides[get_push_manager] = lambda: push_manager
    app.state.redis = test_redis
    app.state.push_manager = push_manager

    from edictum_server.notifications.base import NotificationManager

    app.state.notification_manager = NotificationManager()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/slack/interactions",
                content=body_bytes,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "x-slack-request-timestamp": timestamp,
                    "x-slack-signature": signature,
                },
            )
        # Cross-tenant mismatch must be rejected
        assert resp.status_code == 403, (
            f"Expected 403 for cross-tenant Redis key mismatch, got {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()

    # Verify the approval was NOT decided (still pending)
    async with _test_session_factory() as db:
        result = await db.get(Approval, approval_b_id)
        assert result is not None
        assert result.status == "pending", (
            "Approval should remain pending — cross-tenant decision must be blocked"
        )


@pytest.mark.asyncio
async def test_slack_same_tenant_redis_accepted(
    test_redis: Any,
    push_manager: Any,
) -> None:
    """Slack webhook: Redis tenant_id matches channel tenant_id -> decision accepted.

    Positive test confirming that the cross-check doesn't break the happy path.
    """
    signing_secret = "test-slack-signing-secret-same-tenant"

    async with _test_session_factory() as db:
        channel_a = _make_slack_channel(TENANT_A_ID, signing_secret)
        db.add(channel_a)
        approval_a = _make_approval(TENANT_A_ID)
        db.add(approval_a)
        await db.commit()
        channel_a_id = str(channel_a.id)
        approval_a_id = approval_a.id

    # Redis key correctly stores tenant A's id for tenant A's channel
    await test_redis.set(
        f"slack:tenant:{channel_a_id}:{approval_a_id}",
        str(TENANT_A_ID),
        ex=3600,
    )

    action_id = f"edictum_approve:{approval_a_id}"
    slack_payload = {
        "type": "block_actions",
        "actions": [{"action_id": action_id}],
        "user": {"username": "approver"},
    }
    body_str = urllib.parse.urlencode({"payload": json.dumps(slack_payload)})
    body_bytes = body_str.encode()
    timestamp = str(int(time.time()))
    signature = _slack_signature(signing_secret, timestamp, body_bytes)

    from edictum_server.db.engine import get_db
    from edictum_server.main import app
    from edictum_server.push.manager import get_push_manager
    from edictum_server.redis.client import get_redis
    from tests.conftest import _override_get_db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = lambda: test_redis
    app.dependency_overrides[get_push_manager] = lambda: push_manager
    app.state.redis = test_redis
    app.state.push_manager = push_manager

    from edictum_server.notifications.base import NotificationManager

    app.state.notification_manager = NotificationManager()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/slack/interactions",
                content=body_bytes,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "x-slack-request-timestamp": timestamp,
                    "x-slack-signature": signature,
                },
            )
        # Same-tenant: decision should be accepted (200)
        assert resp.status_code == 200, (
            f"Expected 200 for same-tenant Slack webhook, got {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()

    async with _test_session_factory() as db:
        result = await db.get(Approval, approval_a_id)
        assert result is not None
        assert result.status == "approved"


# ---------------------------------------------------------------------------
# Discord: Redis tenant_id belongs to tenant B, channel belongs to tenant A
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discord_cross_tenant_redis_mismatch_rejected(
    test_redis: Any,
    push_manager: Any,
) -> None:
    """Discord webhook: Redis-resolved tenant_id (B) != channel tenant_id (A) -> Expired embed.

    Attack scenario: tampered Redis key routes a tenant B approval decision
    through tenant A's Discord channel. The cross-check must reject it.
    """
    from nacl.signing import SigningKey as NaclSigningKey

    # Generate a real Ed25519 key pair for tenant A's Discord channel
    signing_key = NaclSigningKey.generate()
    public_key_hex = signing_key.verify_key.encode().hex()

    async with _test_session_factory() as db:
        channel_a = _make_discord_channel(TENANT_A_ID, public_key_hex)
        db.add(channel_a)
        approval_b = _make_approval(TENANT_B_ID)
        db.add(approval_b)
        await db.commit()
        channel_a_id = str(channel_a.id)
        approval_b_id = approval_b.id

    # Seed Redis: tenant B's id under channel A's key (cross-tenant tamper)
    await test_redis.set(
        f"discord:tenant:{channel_a_id}:{approval_b_id}",
        str(TENANT_B_ID),
        ex=3600,
    )

    # Build a valid Discord interaction payload signed with channel A's key
    body_json = {
        "type": 3,  # MESSAGE_COMPONENT
        "data": {"custom_id": f"edictum_approve:{approval_b_id}"},
        "member": {"user": {"username": "attacker"}},
    }
    body_bytes = json.dumps(body_json).encode()
    timestamp = str(int(time.time()))
    message = timestamp.encode() + body_bytes
    signature_hex = signing_key.sign(message).signature.hex()

    from edictum_server.db.engine import get_db
    from edictum_server.main import app
    from edictum_server.push.manager import get_push_manager
    from edictum_server.redis.client import get_redis
    from tests.conftest import _override_get_db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = lambda: test_redis
    app.dependency_overrides[get_push_manager] = lambda: push_manager
    app.state.redis = test_redis
    app.state.push_manager = push_manager

    from edictum_server.notifications.base import NotificationManager

    app.state.notification_manager = NotificationManager()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/discord/interactions",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "x-signature-ed25519": signature_hex,
                    "x-signature-timestamp": timestamp,
                },
            )
        assert resp.status_code == 200
        resp_json = resp.json()
        # Must return the "Approval Expired" embed, not a decided response
        embeds = resp_json.get("data", {}).get("embeds", [])
        assert any("Expired" in (e.get("title") or "") for e in embeds), (
            f"Expected 'Approval Expired' embed for cross-tenant mismatch, got: {resp_json}"
        )
    finally:
        app.dependency_overrides.clear()

    # Verify the approval was NOT decided
    async with _test_session_factory() as db:
        result = await db.get(Approval, approval_b_id)
        assert result is not None
        assert result.status == "pending", (
            "Approval should remain pending — cross-tenant decision must be blocked"
        )


@pytest.mark.asyncio
async def test_discord_same_tenant_redis_accepted(
    test_redis: Any,
    push_manager: Any,
) -> None:
    """Discord webhook: Redis tenant_id matches channel tenant_id -> decision accepted."""
    from nacl.signing import SigningKey as NaclSigningKey

    signing_key = NaclSigningKey.generate()
    public_key_hex = signing_key.verify_key.encode().hex()

    async with _test_session_factory() as db:
        channel_a = _make_discord_channel(TENANT_A_ID, public_key_hex)
        db.add(channel_a)
        approval_a = _make_approval(TENANT_A_ID)
        db.add(approval_a)
        await db.commit()
        channel_a_id = str(channel_a.id)
        approval_a_id = approval_a.id

    await test_redis.set(
        f"discord:tenant:{channel_a_id}:{approval_a_id}",
        str(TENANT_A_ID),
        ex=3600,
    )

    body_json = {
        "type": 3,
        "data": {"custom_id": f"edictum_approve:{approval_a_id}"},
        "member": {"user": {"username": "approver"}},
    }
    body_bytes = json.dumps(body_json).encode()
    timestamp = str(int(time.time()))
    message = timestamp.encode() + body_bytes
    signature_hex = signing_key.sign(message).signature.hex()

    from edictum_server.db.engine import get_db
    from edictum_server.main import app
    from edictum_server.push.manager import get_push_manager
    from edictum_server.redis.client import get_redis
    from tests.conftest import _override_get_db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = lambda: test_redis
    app.dependency_overrides[get_push_manager] = lambda: push_manager
    app.state.redis = test_redis
    app.state.push_manager = push_manager

    from edictum_server.notifications.base import NotificationManager

    app.state.notification_manager = NotificationManager()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/discord/interactions",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "x-signature-ed25519": signature_hex,
                    "x-signature-timestamp": timestamp,
                },
            )
        assert resp.status_code == 200
        resp_json = resp.json()
        embeds = resp_json.get("data", {}).get("embeds", [])
        # Should show a decided response (Approved/Denied), not Expired
        assert not any("Expired" in (e.get("title") or "") for e in embeds), (
            f"Same-tenant Discord webhook should succeed, got: {resp_json}"
        )
    finally:
        app.dependency_overrides.clear()

    async with _test_session_factory() as db:
        result = await db.get(Approval, approval_a_id)
        assert result is not None
        assert result.status == "approved"


# ---------------------------------------------------------------------------
# Telegram: Redis tenant_id belongs to tenant B, channel belongs to tenant A
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_cross_tenant_redis_mismatch_rejected(
    test_redis: Any,
    push_manager: Any,
) -> None:
    """Telegram webhook: Redis-resolved tenant_id (B) != channel tenant_id (A) -> blocked.

    Attack scenario: A Redis key for channel A contains tenant B's UUID.
    Without the cross-check, the handler would submit a decision against
    tenant B's approval using the channel A webhook path.
    """
    webhook_secret = "test-telegram-webhook-secret-channel-a"

    async with _test_session_factory() as db:
        channel_a = _make_telegram_channel(TENANT_A_ID, webhook_secret)
        db.add(channel_a)
        approval_b = _make_approval(TENANT_B_ID)
        db.add(approval_b)
        await db.commit()
        channel_a_id = str(channel_a.id)
        approval_b_id = approval_b.id

    # Tampered Redis: channel A carries tenant B's tenant_id
    await test_redis.set(
        f"telegram:tenant:{channel_a_id}:{approval_b_id}",
        str(TENANT_B_ID),
        ex=3600,
    )

    callback_body = {
        "callback_query": {
            "id": "cbq-123",
            "data": f"approve:{approval_b_id}",
            "from": {"id": 999, "username": "attacker"},
        }
    }

    # Mock the TelegramChannel in the notification manager
    mock_tg_channel = MagicMock()
    mock_tg_channel.channel_id = channel_a_id
    mock_tg_channel.client = MagicMock()
    mock_tg_channel.client.answer_callback_query = AsyncMock()
    mock_tg_channel.update_decision = AsyncMock()

    from edictum_server.notifications.base import NotificationManager

    mock_mgr = MagicMock(spec=NotificationManager)
    mock_mgr.channels = [mock_tg_channel]

    from edictum_server.db.engine import get_db
    from edictum_server.main import app
    from edictum_server.push.manager import get_push_manager
    from edictum_server.redis.client import get_redis
    from tests.conftest import _override_get_db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = lambda: test_redis
    app.dependency_overrides[get_push_manager] = lambda: push_manager
    app.state.redis = test_redis
    app.state.push_manager = push_manager
    app.state.notification_manager = mock_mgr

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Patch _find_telegram_channel to return our mock
            with patch(
                "edictum_server.routes.telegram._find_telegram_channel",
                return_value=mock_tg_channel,
            ):
                resp = await ac.post(
                    f"/api/v1/telegram/webhook/{channel_a_id}",
                    json=callback_body,
                    headers={
                        "x-telegram-bot-api-secret-token": webhook_secret,
                    },
                )
        assert resp.status_code == 200
        # The cross-check must have fired — answer_callback_query called with "expired"
        mock_tg_channel.client.answer_callback_query.assert_called_once()
        call_args = mock_tg_channel.client.answer_callback_query.call_args
        assert (
            "expired" in (call_args[0][1] if call_args[0] else "").lower()
            or "expired" in str(call_args).lower()
        ), f"Expected 'expired' callback answer on tenant mismatch, got: {call_args}"
    finally:
        app.dependency_overrides.clear()

    # Verify the approval was NOT decided
    async with _test_session_factory() as db:
        result = await db.get(Approval, approval_b_id)
        assert result is not None
        assert result.status == "pending", (
            "Approval should remain pending — cross-tenant decision must be blocked"
        )


@pytest.mark.asyncio
async def test_telegram_same_tenant_redis_accepted(
    test_redis: Any,
    push_manager: Any,
) -> None:
    """Telegram webhook: Redis tenant_id matches channel tenant_id -> decision accepted."""
    webhook_secret = "test-telegram-webhook-secret-same-tenant"

    async with _test_session_factory() as db:
        channel_a = _make_telegram_channel(TENANT_A_ID, webhook_secret)
        db.add(channel_a)
        approval_a = _make_approval(TENANT_A_ID)
        db.add(approval_a)
        await db.commit()
        channel_a_id = str(channel_a.id)
        approval_a_id = approval_a.id

    await test_redis.set(
        f"telegram:tenant:{channel_a_id}:{approval_a_id}",
        str(TENANT_A_ID),
        ex=3600,
    )

    callback_body = {
        "callback_query": {
            "id": "cbq-456",
            "data": f"approve:{approval_a_id}",
            "from": {"id": 777, "username": "approver"},
        }
    }

    mock_tg_channel = MagicMock()
    mock_tg_channel.channel_id = channel_a_id
    mock_tg_channel.client = MagicMock()
    mock_tg_channel.client.answer_callback_query = AsyncMock()
    mock_tg_channel.update_decision = AsyncMock()

    from edictum_server.notifications.base import NotificationManager

    mock_mgr = MagicMock(spec=NotificationManager)
    mock_mgr.channels = [mock_tg_channel]

    from edictum_server.db.engine import get_db
    from edictum_server.main import app
    from edictum_server.push.manager import get_push_manager
    from edictum_server.redis.client import get_redis
    from tests.conftest import _override_get_db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = lambda: test_redis
    app.dependency_overrides[get_push_manager] = lambda: push_manager
    app.state.redis = test_redis
    app.state.push_manager = push_manager
    app.state.notification_manager = mock_mgr

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch(
                "edictum_server.routes.telegram._find_telegram_channel",
                return_value=mock_tg_channel,
            ):
                resp = await ac.post(
                    f"/api/v1/telegram/webhook/{channel_a_id}",
                    json=callback_body,
                    headers={
                        "x-telegram-bot-api-secret-token": webhook_secret,
                    },
                )
        assert resp.status_code == 200
        # Same-tenant: update_decision should be called (approval processed)
        mock_tg_channel.update_decision.assert_called_once()
    finally:
        app.dependency_overrides.clear()

    async with _test_session_factory() as db:
        result = await db.get(Approval, approval_a_id)
        assert result is not None
        assert result.status == "approved"
