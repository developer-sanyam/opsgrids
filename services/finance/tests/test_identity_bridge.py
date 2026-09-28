"""The identity bridge: `services.ensure_customer_for_crm_contact`, and the internal route that
calls it, `POST /internal/v1/organizations/{org}/customers:resolve-for-crm-contact`.

Properties worth protecting: the replay path (an already-bridged `crm_contact_id`) makes no
network call at all; a genuine first resolve creates exactly one `Customer` row and sets
`crm_contact_id`; two concurrent first resolves for the same contact still produce exactly one
row (a real concurrency test — two independent sessions, not a sequential stand-in); a customer-
service outage is a clean, typed, retryable failure, never a placeholder customer; and the
network call itself never runs with a database transaction open (D3), which `tests/infra/
test_io_outside_transaction.py` at the repo root also checks statically.

The route's own auth/authz shape matches every other caller-restricted internal route on the
platform (`services/customer`'s `leads:convert`): a service token is required, the caller must
be named `operations`, and the token's `org` claim must name the path's organization.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, Customer, OutboxEvent
from platform_core.db import TENANT_GUC
from platform_core.errors import NotFoundProblem, UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


def _crm_contact_handler(
    *,
    contact_id: uuid.UUID,
    full_name: str = "Priya Sharma",
    email: str = "priya@stellar.example",
    company_id: uuid.UUID | None = None,
    company_name: str = "Stellar Robotics",
    calls: list[int] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(1)
        return httpx.Response(
            200,
            json={
                "contact_id": str(contact_id),
                "full_name": full_name,
                "email": email,
                "company_id": str(company_id or uuid.uuid4()),
                "company_name": company_name,
            },
        )

    return handler


def _unavailable_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("customer-service is unreachable", request=request)


def _not_found_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={"type": "https://opsgrids.example/problems/not-found"})


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestEnsureCustomerForCrmContact:
    async def test_the_first_call_creates_a_customer_and_sets_crm_contact_id(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = services.crm_bridge_ctx(org_id)
        crm_contact_id = uuid.uuid4()
        async with _mock_client(
            _crm_contact_handler(
                contact_id=crm_contact_id,
                full_name="Priya Sharma",
                email="priya@stellar.example",
                company_name="Stellar Robotics",
            )
        ) as client:
            customer = await services.ensure_customer_for_crm_contact(
                session, ctx, crm_contact_id, client=client
            )

        assert customer.crm_contact_id == crm_contact_id
        assert customer.name == "Stellar Robotics"
        assert customer.contact_name == "Priya Sharma"
        assert customer.email == "priya@stellar.example"

        async with session.begin():
            row = await selectors.get_customer_by_crm_contact(session, org_id, crm_contact_id)
        assert row is not None
        assert row.id == customer.id

    async def test_no_company_name_falls_back_to_the_contacts_full_name(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = services.crm_bridge_ctx(org_id)
        crm_contact_id = uuid.uuid4()
        async with _mock_client(
            _crm_contact_handler(
                contact_id=crm_contact_id, full_name="Arjun Verma", company_name=""
            )
        ) as client:
            customer = await services.ensure_customer_for_crm_contact(
                session, ctx, crm_contact_id, client=client
            )
        assert customer.name == "Arjun Verma"

    async def test_a_second_call_for_the_same_contact_returns_the_same_row_with_no_second_call(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = services.crm_bridge_ctx(org_id)
        crm_contact_id = uuid.uuid4()
        calls: list[int] = []

        async with _mock_client(
            _crm_contact_handler(contact_id=crm_contact_id, calls=calls)
        ) as client:
            first = await services.ensure_customer_for_crm_contact(
                session, ctx, crm_contact_id, client=client
            )
            second = await services.ensure_customer_for_crm_contact(
                session, ctx, crm_contact_id, client=client
            )

        assert first.id == second.id
        assert len(calls) == 1, "a replay must not ask customer-service again"

        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(Customer)
                    .where(
                        Customer.organization_id == org_id,
                        Customer.crm_contact_id == crm_contact_id,
                    )
                )
            ).scalar_one()
        assert count == 1

    async def test_creating_a_customer_stages_the_created_event(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = services.crm_bridge_ctx(org_id)
        crm_contact_id = uuid.uuid4()
        async with _mock_client(_crm_contact_handler(contact_id=crm_contact_id)) as client:
            customer = await services.ensure_customer_for_crm_contact(
                session, ctx, crm_contact_id, client=client, acting_as="operations"
            )

        async with session.begin():
            row = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.event_type == services.EVT_CUSTOMER_CREATED,
                        OutboxEvent.aggregate_id == customer.id,
                    )
                )
            ).scalar_one()
        assert row.payload["crm_contact_id"] == str(crm_contact_id)

    async def test_customer_service_unavailable_is_a_clean_typed_failure(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = services.crm_bridge_ctx(org_id)
        crm_contact_id = uuid.uuid4()
        async with _mock_client(_unavailable_handler) as client:
            with pytest.raises(UpstreamUnavailableProblem):
                await services.ensure_customer_for_crm_contact(
                    session, ctx, crm_contact_id, client=client
                )

        # Never a placeholder row.
        async with session.begin():
            row = await selectors.get_customer_by_crm_contact(session, org_id, crm_contact_id)
        assert row is None

    async def test_an_unknown_crm_contact_is_a_clean_typed_not_found(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = services.crm_bridge_ctx(org_id)
        crm_contact_id = uuid.uuid4()
        async with _mock_client(_not_found_handler) as client:
            with pytest.raises(NotFoundProblem):
                await services.ensure_customer_for_crm_contact(
                    session, ctx, crm_contact_id, client=client
                )

    async def test_two_concurrent_first_resolves_produce_exactly_one_customer(
        self, org_id: uuid.UUID
    ) -> None:
        """A real concurrency test: two independent sessions on two independent connections,
        both racing the lookup-then-create path for the same `crm_contact_id`. The pre-check
        inside `ensure_customer_for_crm_contact` narrows this to a rare race, not eliminates
        it — the same reasoning `test_lead_dedup_race.py` documents for lead-service's own
        `create_lead`."""
        cfg = settings()
        crm_contact_id = uuid.uuid4()
        engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
        maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

        def _bind(org: uuid.UUID):  # type: ignore[no-untyped-def]
            def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

            return _listener

        ctx = services.crm_bridge_ctx(org_id)

        # A plain `asyncio.gather` leaves the actual interleaving to the event loop — both
        # tasks might genuinely race the insert, or one might sail through claim/call/record
        # entirely before the other's claim step even runs, which never exercises the
        # `IntegrityError`-on-`uq_customers_org_crm_contact` path at all. The barrier pins the
        # rendezvous to the one place that matters: neither task leaves its network call (the
        # only await between the claim and record transactions, per D3) until both have made
        # it, so both enter the record transaction back to back on every run, not just most.
        contact_handler = _crm_contact_handler(contact_id=crm_contact_id)
        barrier = asyncio.Barrier(2)

        async def _synced_handler(request: httpx.Request) -> httpx.Response:
            await barrier.wait()
            return contact_handler(request)

        async def _resolve(sess: AsyncSession) -> Customer:
            async with httpx.AsyncClient(transport=httpx.MockTransport(_synced_handler)) as client:
                return await services.ensure_customer_for_crm_contact(
                    sess, ctx, crm_contact_id, client=client
                )

        try:
            async with maker_a() as session_a, maker_b() as session_b:
                sa_event.listen(session_a.sync_session, "after_begin", _bind(org_id))
                sa_event.listen(session_b.sync_session, "after_begin", _bind(org_id))

                result_a, result_b = await asyncio.gather(_resolve(session_a), _resolve(session_b))
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

        assert result_a.id == result_b.id

        check_engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        try:
            check_maker = async_sessionmaker(check_engine, expire_on_commit=False)
            async with check_maker() as check:
                sa_event.listen(check.sync_session, "after_begin", _bind(org_id))
                async with check.begin():
                    count = (
                        await check.execute(
                            select(func.count())
                            .select_from(Customer)
                            .where(
                                Customer.organization_id == org_id,
                                Customer.crm_contact_id == crm_contact_id,
                            )
                        )
                    ).scalar_one()
        finally:
            await check_engine.dispose()
        assert count == 1


def _token(org_id: uuid.UUID, *, caller: str = "operations") -> str:
    return mint_service_token(
        settings(), audience="finance", caller=caller, claims={"org": str(org_id)}
    )


class TestResolveForCrmContactInternalRoute:
    URL_TEMPLATE = "/internal/v1/organizations/{org_id}/customers:resolve-for-crm-contact"

    def _url(self, org_id: uuid.UUID) -> str:
        return self.URL_TEMPLATE.format(org_id=org_id)

    async def test_without_a_service_token_it_is_refused(self, client, org_id) -> None:
        resp = await client.post(self._url(org_id), json={"crm_contact_id": str(uuid.uuid4())})
        assert resp.status_code == 401

    async def test_a_caller_other_than_operations_is_refused(self, client, org_id) -> None:
        resp = await client.post(
            self._url(org_id),
            headers={"X-Internal-Token": _token(org_id, caller="lead")},
            json={"crm_contact_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 403

    async def test_a_token_addressed_to_another_organization_is_refused(
        self, client, org_id
    ) -> None:
        other_org = uuid.uuid4()
        resp = await client.post(
            self._url(org_id),
            headers={"X-Internal-Token": _token(other_org)},
            json={"crm_contact_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 403

    async def test_the_happy_path_creates_and_returns_a_customer_id(
        self, client, org_id, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        crm_contact_id = uuid.uuid4()
        handler = _crm_contact_handler(contact_id=crm_contact_id, full_name="Priya Sharma")
        original = httpx.AsyncClient

        def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
            kwargs.pop("timeout", None)
            return original(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(httpx, "AsyncClient", patched)

        resp = await client.post(
            self._url(org_id),
            headers={"X-Internal-Token": _token(org_id)},
            json={"crm_contact_id": str(crm_contact_id)},
        )
        assert resp.status_code == 200
        customer_id = resp.json()["customer_id"]

        async with session.begin():
            row = await selectors.get_customer_by_crm_contact(session, org_id, crm_contact_id)
        assert row is not None
        assert str(row.id) == customer_id

        # A second call for the same contact returns the same id.
        resp2 = await client.post(
            self._url(org_id),
            headers={"X-Internal-Token": _token(org_id)},
            json={"crm_contact_id": str(crm_contact_id)},
        )
        assert resp2.status_code == 200
        assert resp2.json()["customer_id"] == customer_id

    async def test_an_unknown_field_is_refused(self, client, org_id) -> None:
        resp = await client.post(
            self._url(org_id),
            headers={"X-Internal-Token": _token(org_id)},
            json={"crm_contact_id": str(uuid.uuid4()), "extra": "nope"},
        )
        assert resp.status_code == 422
