"""What is retryable, what is permanent, and what is a defect — at the webhook's edge.

A status code is a promise to the provider about whether a retry can help:

* **503** — the database is not there. Every way that looks from this route (a refused connection,
  a DNS failure, asyncpg's "not now" and "too many connections") must say so, or the provider sees a
  500 and the operator a defect where there is an outage.
* **400** — a signature that does not verify, whatever bytes it is made of.
* **not 2xx, and not `ignored`** — anything unexpected. A defect is a 500, an unrelated integrity
  error the platform's 409; neither may be recorded as a permanent condition and answered 200,
  which would turn a bug into silently lost payments.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from asyncpg import exceptions as pg_exc  # type: ignore[import-untyped]
from httpx import AsyncClient
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.main import app
from app.providers.razorpay import RazorpayProvider
from platform_core.db import get_session
from tests.helpers import (
    audit_rows,
    charge_row,
    ignored_audits,
    inbox_rows,
    post_webhook,
    seed_charge,
    webhook_body,
)

pytestmark = pytest.mark.integration

WEBHOOK = "/api/v1/webhooks/razorpay"


class TestTheDatabaseBeingUnreachableIsA503:
    async def test_a_refused_connection_is_a_503_not_a_500(
        self,
        client: AsyncClient,
        tolerant: AsyncClient,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """The common outage: nothing is listening. The driver raises a raw `OSError` from
        `connect()`, not a SQLAlchemy `OperationalError` — which the route used to answer 500. The
        session here really cannot connect (port 1), so nothing is faked below the route."""
        charge = await seed_charge(client, org_id, key="down-at-connect")
        body = webhook_body(charge, org_id, event="payment.captured")
        # The credentials of the test DSN, pointed at a port nothing listens on.
        dsn = settings().app_dsn.rsplit("@", 1)[0] + "@127.0.0.1:1/unreachable"
        engine = create_async_engine(dsn)
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        async def unreachable() -> AsyncIterator[AsyncSession]:
            async with maker() as s:
                yield s

        saved = app.dependency_overrides[get_session]
        app.dependency_overrides[get_session] = unreachable
        try:
            res = await post_webhook(tolerant, provider, body, "evt_down")
        finally:
            app.dependency_overrides[get_session] = saved
            await engine.dispose()

        assert res.status_code == 503, res.text
        assert res.json()["type"].endswith("/upstream-unavailable")
        assert "127.0.0.1" not in res.text and "refused" not in res.text.lower()

    @pytest.mark.parametrize(
        "raised",
        [
            ConnectionRefusedError(111, "Connection refused"),
            OSError(113, "No route to host"),
            TimeoutError("connect timed out"),
            pg_exc.PostgresConnectionError("connection was closed in the middle of operation"),
            pg_exc.ConnectionDoesNotExistError("connection was closed"),
            pg_exc.CannotConnectNowError("the database system is starting up"),
            pg_exc.TooManyConnectionsError("sorry, too many clients already"),
        ],
        ids=lambda e: type(e).__name__,
    )
    async def test_every_way_the_database_can_be_away_is_a_503_and_leaves_nothing_behind(
        self,
        client: AsyncClient,
        tolerant: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        raised: BaseException,
    ) -> None:
        charge = await seed_charge(client, org_id, key=f"away-{type(raised).__name__}")
        body = webhook_body(charge, org_id, event="payment.captured")

        async def away(*_a: Any, **_k: Any) -> None:
            raise raised

        with monkeypatch.context() as patch:
            patch.setattr(services, "_resolve_charge", away)
            res = await post_webhook(tolerant, provider, body, "evt_away")

        assert res.status_code == 503, res.text
        assert await inbox_rows(admin_session, org_id) == []
        assert (await charge_row(admin_session, charge["id"]))["state"] == "authorized"
        # The event id is not burned: the redelivery, the database back, is a first delivery.
        back = await post_webhook(tolerant, provider, body, "evt_away")
        assert back.status_code == 200 and back.json()["reconciled"] is True


class TestADefectIsNeverAnOutage:
    @pytest.mark.parametrize(
        "raised",
        [
            RuntimeError("a defect"),
            ValueError("bad value"),
            KeyError("missing"),
            DataError("INSERT", {}, Exception("value too long for type character varying(120)")),
            ProgrammingError("SELECT", {}, Exception('relation "payment.nope" does not exist')),
        ],
        ids=lambda e: type(e).__name__,
    )
    async def test_it_stays_a_500(
        self,
        client: AsyncClient,
        tolerant: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        raised: BaseException,
    ) -> None:
        charge = await seed_charge(client, org_id, key=f"defect-{type(raised).__name__}")
        body = webhook_body(charge, org_id, event="payment.captured")

        async def broken(*_a: Any, **_k: Any) -> None:
            raise raised

        monkeypatch.setattr(services, "_reconcile_charge", broken)
        res = await post_webhook(tolerant, provider, body, "evt_defect")

        assert res.status_code == 500
        assert "upstream-unavailable" not in res.text
        assert await inbox_rows(admin_session, org_id) == []
        assert ignored_audits(await audit_rows(admin_session, org_id)) == []


class TestAnUnrelatedIntegrityErrorIsNotAConflict:
    async def test_it_is_the_platforms_409_never_a_200_ignored(
        self,
        client: AsyncClient,
        tolerant: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only the two provider-id unique indexes are a permanent conflict
        (`provider_id_conflict`). Any other integrity error — here a primary key — is a bug or a
        race, and must not be recorded as `ignored` and answered 200. What the platform really
        answers is **409** (`register_error_handlers` maps every `IntegrityError` to a conflict
        problem): non-2xx, so the provider retries, with the constraint's name kept out of the
        body. (The docs had said 500; this pins the real behaviour.)"""
        charge = await seed_charge(client, org_id, key="other-integrity")
        body = webhook_body(charge, org_id, event="payment.captured")

        async def boom(*_a: Any, **_k: Any) -> bool:
            raise IntegrityError(
                "INSERT",
                {},
                Exception('duplicate key value violates unique constraint "pk_outbox"'),
            )

        monkeypatch.setattr(services, "_reconcile_charge", boom)
        res = await post_webhook(tolerant, provider, body, "evt_integrity")

        assert res.status_code == 409, res.text
        assert "ignored_reason" not in res.text and "pk_outbox" not in res.text
        assert await inbox_rows(admin_session, org_id) == []
        assert ignored_audits(await audit_rows(admin_session, org_id)) == []


class TestASignatureMadeOfAnyBytes:
    @pytest.mark.parametrize(
        "signature",
        [b"\xff" * 64, b"\xc3\xa9" * 32, b"not-hex-at-all", b"0" * 64],
        ids=["latin1-high-bytes", "utf8-accent", "ascii-junk", "hex-but-wrong"],
    )
    async def test_is_a_400_never_a_500(
        self,
        client: AsyncClient,
        tolerant: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        signature: bytes,
    ) -> None:
        """`hmac.compare_digest` on a non-ASCII `str` raises `TypeError` — so an unauthenticated
        request could turn any webhook into a 500. Now it is what it is: a signature that does not
        verify."""
        charge = await seed_charge(client, org_id, key=f"sig-{signature[:4].hex()}")
        body = webhook_body(charge, org_id, event="payment.captured")

        res = await tolerant.post(
            WEBHOOK,
            content=body,
            headers={"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_sig"},
        )

        assert res.status_code == 400, res.text
        assert res.json()["type"].endswith("/invalid-webhook-signature")
        assert await inbox_rows(admin_session, org_id) == []
        assert (await charge_row(admin_session, charge["id"]))["state"] == "authorized"
