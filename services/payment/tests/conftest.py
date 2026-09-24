"""Fixtures for payment-service tests.

Same shape as billing- and metering-service, and for the same reasons: real PostgreSQL (the schema
leans on row-level security and an append-only trigger SQLite cannot model), the service's **app**
role rather than the superuser (so a policy or a trigger that is inert reads as a failure here
rather than a false pass — the outcome ADR-0002 calls worse than no test), and the tenant bound on
every transaction because the GUC is transaction-local.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a tenant needs
no row in any other service — a bare UUID is a whole tenant.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so only one
# may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.main import app
from app.models import SCHEMA
from app.providers.razorpay import RazorpayProvider
from platform_core.db import (
    TENANT_GUC,
    dispose_engine,
    dispose_outbox_engine,
    get_session,
    init_engine,
    init_outbox_engine,
)

#: Everything payment owns. Tenant-scoped or global-and-test-empty, so all are safe to truncate.
#: `refunds` before `charges` for the foreign key; TRUNCATE CASCADE would handle it too.
PAYMENT_TABLES = (
    "refunds",
    "provider_events",
    "charges",
    "mandates",
    "audit_logs",
    "outbox",
    "processed_events",
)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    """The primary tenant. A bare UUID is the whole of it — no cross-schema row is needed."""
    return uuid.uuid4()


@pytest.fixture
def org_b() -> uuid.UUID:
    """A second tenant, for isolation tests."""
    return uuid.uuid4()


@pytest.fixture
def provider() -> RazorpayProvider:
    """The real Razorpay adapter, built with **no keys** — exactly the development posture. The
    webhook secret matches the service settings, so a test can sign a body the app will verify."""
    cfg = settings()
    return RazorpayProvider(
        key_id=None,
        key_secret=None,
        webhook_secret=cfg.razorpay_webhook_secret.get_secret_value(),
    )


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """The **app** role, every transaction bound to `org_id` (reapplied on each `begin()` because
    the GUC is transaction-local). This mirrors what the request chokepoints do.

    Pointing this at the admin or superuser DSN would make row-level security and the append-only
    trigger invisible here and every isolation assertion a false pass."""
    cfg = settings()
    tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in PAYMENT_TABLES)

    admin = create_async_engine(cfg.admin_dsn, connect_args=_connect_args())
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await admin.dispose()

    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
            # A SAVEPOINT (`begin_nested`) fires this event too, but it is not a new transaction:
            # the GUC set for the outer one still holds, and re-applying the *fixture's*
            # organization here would clobber the tenant a route bound for itself (the webhook
            # binds the organization its body names, which a test may make another tenant's).
            if transaction.nested:
                return
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def engines() -> AsyncIterator[None]:
    """The real process-wide application and **outbox** engines, for `app/cron.py`'s
    stuck-charge sweep: its discovery query runs on `get_outbox_sessionmaker`, which is `None`
    (and silently falls back to the app engine — the untested, unsplit configuration) until
    `init_outbox_engine` has run.

    Per test rather than per session: an engine binds itself to the event loop it was created on,
    and pytest-asyncio gives each test a fresh one.
    """
    cfg = settings()
    init_engine(cfg)
    init_outbox_engine(cfg)
    yield
    await dispose_outbox_engine()
    await dispose_engine()


@pytest_asyncio.fixture
async def admin_session() -> AsyncIterator[AsyncSession]:
    """A BYPASSRLS session, for seeding another tenant's rows in an isolation test and for reading
    the policied `outbox`/`audit_logs` across a binding when proving an event was staged.

    Reaching for it to make a tenant-scoped test pass would hide exactly what the policies exist to
    catch — so it is used only where a second organization's data must exist, or where a
    cross-binding read is the point."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession, provider: RazorpayProvider) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session and the test's provider.

    Both the webhook and the internal charge route wrap `get_session`, so overriding that one
    dependency routes every route through the shared session while leaving each route's own tenant
    binding intact. `get_provider` is overridden so the app verifies signatures with the same
    secret the test signs with."""
    from app.deps import get_provider

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_provider] = lambda: provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def tolerant(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """A client that answers what the server would — a 500 is a 500, not an exception in the test.
    Built after `client` so the app's dependency overrides (the shared session, the test provider)
    are in place."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
