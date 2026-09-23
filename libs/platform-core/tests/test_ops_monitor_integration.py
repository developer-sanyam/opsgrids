"""The checks against real Postgres, RabbitMQ and MailHog (M2/M3).

**Never the shared `opsgrids` database.** This suite targets `opsgrids_mon`, a second
database in the same `make infra` cluster created by `make ops-monitor-db`
(`tools/dev-bootstrap/ops-monitor-db.sql`) — the same shape `opsgrids_wt` already is for the
e2e suite. `account` and `product` are migrated into it (two services is enough to exercise a
real, multi-schema outbox-lag scan) so this suite never contends with another developer's rows
in the shared database's `account`/`product` schemas.

RabbitMQ: a dedicated vhost, created and torn down per test through the management HTTP API
(the `rabbitmq:3-management-alpine` image `make infra` already runs) — never the default `/`
vhost every other suite's DLQ would collide with.

Needs, once — a worktree-local `DATABASE_URL` pointed at `opsgrids_mon` instead of `opsgrids`:

    make infra
    make ops-monitor-db
    cd services/account && DATABASE_URL=$MON_DSN uv run alembic upgrade head
    cd services/product && DATABASE_URL=$MON_DSN uv run alembic upgrade head

where `$MON_DSN` is `postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/opsgrids_mon`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from platform_core.config import ServiceSettings
from platform_core.edge_relay import SchemaSpec, _make_outbox_model
from platform_core.ops_monitor import checks
from platform_core.ops_monitor.config import OpsMonitorSettings
from platform_core.ops_monitor.mailer import Mailer
from platform_core.ops_monitor.main import format_email
from platform_core.ops_monitor.state import MonitorState, Notification, Status, evaluate

pytestmark = pytest.mark.integration

MON_DATABASE = "opsgrids_mon"
RABBITMQ_MGMT_URL = "http://opsgrids:opsgrids@localhost:15673"
MAILHOG_SMTP_PORT = 1026
MAILHOG_API = "http://localhost:8026"


class _MonSettings(ServiceSettings):
    service_name: str = "ops-monitor-test"
    db_schema: str = "public"


def _mon_dsn(role: str | None) -> str:
    """`DATABASE_URL` with the database swapped to `opsgrids_mon` — never the shared
    `opsgrids` database this monitor's own integration tests must not touch."""
    base = _MonSettings().dsn_as(role)
    return make_url(base).set(database=MON_DATABASE).render_as_string(hide_password=False)


def _admin_dsn() -> str:
    return _mon_dsn("opsgrids_admin")


def _outbox_dsn() -> str:
    return _mon_dsn("opsgrids_outbox")


@pytest_asyncio.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_admin_dsn())
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def outbox_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_outbox_dsn())
    yield engine
    await engine.dispose()


async def _insert_stale_row(admin_engine: AsyncEngine, schema: str, *, age: timedelta) -> uuid.UUID:
    cls = _make_outbox_model(schema)
    sessionmaker = async_sessionmaker(admin_engine, expire_on_commit=False, class_=AsyncSession)
    async with sessionmaker() as session, session.begin():
        row = cls(
            organization_id=uuid.uuid4(),
            event_type="ops_monitor.integration_test",
            aggregate_type="probe",
            aggregate_id=uuid.uuid4(),
            payload={},
            created_at=datetime.now(UTC) - age,
        )
        session.add(row)
        await session.flush()
        return row.id  # type: ignore[no-any-return]


async def _delete_row(admin_engine: AsyncEngine, schema: str, row_id: uuid.UUID) -> None:
    statement = f'DELETE FROM "{schema}"."outbox" WHERE id = :id'  # noqa: S608 — schema is a hardcoded test fixture, not input
    async with admin_engine.begin() as conn:
        await conn.execute(text(statement), {"id": str(row_id)})


class TestOutboxLagFiring:
    async def test_a_stale_row_in_an_isolated_schema_reaches_firing(
        self, admin_engine: AsyncEngine, outbox_engine: AsyncEngine
    ) -> None:
        row_id = await _insert_stale_row(admin_engine, "account", age=timedelta(minutes=20))
        try:
            observations = await checks.check_outbox_lag(
                outbox_engine,
                [SchemaSpec(schema="account", producer="account")],
                warn_seconds=300,
                crit_seconds=900,
                max_attempts=50,
            )
            assert observations["outbox.account"].ok is False
            assert "crit" in observations["outbox.account"].detail

            state = MonitorState()
            now = datetime(2026, 1, 1, tzinfo=UTC)
            notifications: list[Notification] = []
            for i in range(3):
                state, notifications = evaluate(
                    state, observations, now + timedelta(minutes=i), pending_threshold=3
                )
            assert state.checks["outbox.account"].status is Status.FIRING
            assert len(notifications) == 1
            assert notifications[0].kind == "firing"
        finally:
            await _delete_row(admin_engine, "account", row_id)

    async def test_a_missing_schema_is_skipped_not_alarmed(
        self, outbox_engine: AsyncEngine
    ) -> None:
        observations = await checks.check_outbox_lag(
            outbox_engine,
            [SchemaSpec(schema="does_not_exist_in_opsgrids_mon", producer="x")],
            warn_seconds=300,
            crit_seconds=900,
            max_attempts=50,
        )
        assert observations == {}

    async def test_two_schemas_are_scanned_independently(
        self, admin_engine: AsyncEngine, outbox_engine: AsyncEngine
    ) -> None:
        stale_id = await _insert_stale_row(admin_engine, "account", age=timedelta(minutes=20))
        fresh_id = await _insert_stale_row(admin_engine, "product", age=timedelta(seconds=1))
        try:
            observations = await checks.check_outbox_lag(
                outbox_engine,
                [
                    SchemaSpec(schema="account", producer="account"),
                    SchemaSpec(schema="product", producer="product"),
                ],
                warn_seconds=300,
                crit_seconds=900,
                max_attempts=50,
            )
            assert observations["outbox.account"].ok is False
            assert observations["outbox.product"].ok is True
        finally:
            await _delete_row(admin_engine, "account", stale_id)
            await _delete_row(admin_engine, "product", fresh_id)


@pytest_asyncio.fixture
async def dlq_vhost() -> AsyncIterator[str]:
    """A RabbitMQ vhost that exists for exactly one test — never the default `/` vhost every
    other suite's queues live on."""
    name = f"ops-monitor-test-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=RABBITMQ_MGMT_URL, timeout=10) as client:
        await client.put(f"/api/vhosts/{name}")
        await client.put(
            f"/api/permissions/{name}/opsgrids",
            json={"configure": ".*", "write": ".*", "read": ".*"},
        )
        try:
            yield name
        finally:
            await client.delete(f"/api/vhosts/{name}")


def _amqp_url(vhost: str) -> str:
    return f"amqp://opsgrids:opsgrids@localhost:5673/{vhost}"


class TestDlqDepth:
    async def test_a_published_dlq_message_reaches_firing(self, dlq_vhost: str) -> None:
        amqp_url = _amqp_url(dlq_vhost)
        connection = await aio_pika.connect_robust(amqp_url)
        async with connection:
            channel = await connection.channel()
            queue = await channel.declare_queue("probe.events.dlq", durable=True)
            await channel.default_exchange.publish(
                aio_pika.Message(body=b"{}"), routing_key=queue.name
            )

        state = MonitorState()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        notifications: list[Notification] = []
        for i in range(3):
            observations = await checks.check_dlq_depth(amqp_url, ["probe.events"])
            assert observations["dlq.probe.events"].ok is False
            state, notifications = evaluate(
                state, observations, now + timedelta(minutes=i), pending_threshold=3
            )
        assert state.checks["dlq.probe.events"].status is Status.FIRING
        assert len(notifications) == 1

    async def test_a_queue_that_has_never_been_declared_is_ok(self, dlq_vhost: str) -> None:
        amqp_url = _amqp_url(dlq_vhost)
        observations = await checks.check_dlq_depth(amqp_url, ["never.deployed"])
        assert observations["dlq.never.deployed"].ok is True
        assert "not declared yet" in observations["dlq.never.deployed"].detail


class TestMailhogDelivery:
    """Direct SMTP to MailHog — the same transport `mailer.py` uses in production, pointed at
    a box's real `SMTP_*` values instead of Google's submission server."""

    @pytest_asyncio.fixture(autouse=True)
    async def _clear_mailhog(self) -> AsyncIterator[None]:
        async with httpx.AsyncClient(base_url=MAILHOG_API, timeout=10) as client:
            await client.delete("/api/v1/messages")
        yield

    def _mailer(self) -> Mailer:
        settings = OpsMonitorSettings(
            smtp_host="localhost",
            smtp_port=MAILHOG_SMTP_PORT,
            smtp_use_tls=False,
            email_from="ops-monitor@opsgrids.local",
            ops_alert_email="ops@opsgrids.local",
        )
        return Mailer(settings)

    async def _messages(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=MAILHOG_API, timeout=10) as client:
            response = await client.get("/api/v2/messages")
            response.raise_for_status()
            return list(response.json()["items"])

    async def test_a_firing_notification_is_delivered(self) -> None:
        mailer = self._mailer()
        note = Notification(
            check="outbox.account", kind="firing", detail="oldest 950s >= crit 900s"
        )
        subject, body = format_email([note])
        assert await mailer.send(subject, body) is True

        messages = await self._messages()
        assert len(messages) == 1
        assert subject in messages[0]["Content"]["Headers"]["Subject"][0]
        assert "outbox.account" in messages[0]["Content"]["Body"]

    async def test_a_recovery_notification_is_delivered(self) -> None:
        mailer = self._mailer()
        note = Notification(check="outbox.account", kind="resolved", detail="back to normal")
        subject, body = format_email([note])
        assert await mailer.send(subject, body) is True

        messages = await self._messages()
        assert len(messages) == 1
        assert "resolved" in messages[0]["Content"]["Headers"]["Subject"][0]
        assert "back to normal" in messages[0]["Content"]["Body"]

    async def test_send_fails_gracefully_when_smtp_is_unset(self) -> None:
        mailer = Mailer(OpsMonitorSettings())
        assert await mailer.send("subject", "body") is False
        assert (await self._messages()) == []
