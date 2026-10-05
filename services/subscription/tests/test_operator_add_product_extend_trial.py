"""Staff Add product and Extend trial on subscription's side (ADR-0030, the 2026-10-06 design
`docs/superpowers/specs/2026-10-06-admin-add-product-extend-trial-design.md`).

Real PostgreSQL, the real internal routes with a real service token, a fake catalog and registry.
What is pinned:

* **Add product** (`POST /internal/v1/subscriptions`): a row for the product in *any* state —
  `cancelled` included — is a 409 `already_subscribed`; a `one_time` version is a 422 `one_time`;
  a currency other than the organization's live subscriptions' is a 422 `org_currency_mismatch`
  (measured within the named organization only); a trial is the version's own, and a paid add
  claims its first period in the same transaction.
* **Extend trial** (`POST /internal/v1/subscriptions/{id}/extend-trial`): console only; absolute
  target guarded by the end the operator saw, so a replay adds nothing; refused unless trialling,
  while the run holds a claim, past the 90-day total cap, or when it is not an extension; one
  `trial -> trial` transition typed `trial_extended` (never `trial_started`), with the note on the
  audit row only — never on the event — and `previous_trial_ends_at` on the event.
* **Cross-tenant**: another organization's name on this organization's subscription id is a 404
  that writes nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, services
from app.api.internal import get_product_registry
from app.config import settings
from app.main import app
from app.models import (
    PERIOD_PENDING,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
    AuditLog,
    BillingPeriod,
    OutboxEvent,
    Subscription,
    SubscriptionTransition,
)
from app.schemas import SubscribeIn
from platform_core.auth import Scope, ScopeContext
from platform_core.internal_auth import mint_service_token
from tests.helpers import FakeCatalog, FakeRegistry, customer_principal, make_bundle, make_version

pytestmark = pytest.mark.integration

OPERATOR = "operator:22222222-2222-2222-2222-222222222222"
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(autouse=True)
def _registry() -> Iterator[None]:
    app.dependency_overrides[get_product_registry] = lambda: FakeRegistry()
    yield
    app.dependency_overrides.pop(get_product_registry, None)


def _headers(caller: str = "console") -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(settings(), audience="subscription", caller=caller)
    }


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(
        principal=customer_principal(org_id),
        scope=Scope.ORG,
        permission="core.product.activation.write",
    )


def _code(response: Any) -> str | None:
    errors = response.json().get("errors") or []
    return errors[0].get("code") if errors else None


async def _seed(
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
    *,
    product_key: str = "hrms",
    trial: bool = False,
    currency: str = "INR",
    **version_kwargs: Any,
) -> tuple[uuid.UUID, Any]:
    """A subscription as the customer would open one — the row an operator add must respect."""
    version = catalog.add(
        make_version(product_key=product_key, currency=currency, **version_kwargs)
    )
    async with session.begin():
        sub = await services.subscribe(
            session,
            _ctx(org_id),
            catalog,
            SubscribeIn(
                product_key=product_key,
                plan_version_id=version.id,
                trial_days=14 if trial else None,
            ),
        )
        sub_id = sub.id
    # The shared session must not answer a later request from its identity map: a column a test
    # then moves behind its back (`_force`) would read stale.
    session.expunge_all()
    return sub_id, version


async def _read(session: AsyncSession, subscription_id: uuid.UUID) -> Subscription:
    session.expunge_all()
    async with session.begin():
        return await services.require_subscription(session, subscription_id)


async def _force(admin: AsyncSession, subscription_id: uuid.UUID, **columns: Any) -> None:
    await admin.execute(
        update(Subscription).where(Subscription.id == subscription_id).values(**columns)
    )
    await admin.commit()


async def _counts(admin: AsyncSession) -> tuple[int, int, int, int, int]:
    """Subscriptions, audit, outbox, transition and billing-period rows — a refusal writes none."""
    admin.expunge_all()

    async def _n(model: Any) -> int:
        return int((await admin.execute(select(func.count()).select_from(model))).scalar_one())

    return (
        await _n(Subscription),
        await _n(AuditLog),
        await _n(OutboxEvent),
        await _n(SubscriptionTransition),
        await _n(BillingPeriod),
    )


async def _events(admin: AsyncSession, org_id: uuid.UUID) -> list[OutboxEvent]:
    admin.expunge_all()
    rows = await admin.execute(
        select(OutboxEvent)
        .where(OutboxEvent.organization_id == org_id)
        .order_by(OutboxEvent.created_at)
    )
    return list(rows.scalars().all())


async def _add(
    client: AsyncClient,
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    product_key: str = "crm",
    trial: bool = True,
    caller: str | None = "console",
) -> Any:
    return await client.post(
        "/internal/v1/subscriptions",
        json={
            "organization_id": str(org_id),
            "product_key": product_key,
            "plan_version_id": str(version_id),
            "trial": trial,
            "operator_id": OPERATOR,
            "note": "Agreed on the onboarding call",
        },
        headers={} if caller is None else _headers(caller),
    )


# ============================================================================ add product


class TestAddProduct:
    @pytest.mark.parametrize(
        "state", [STATE_TRIAL, STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED, STATE_CANCELLED]
    )
    async def test_any_existing_row_for_the_product_is_a_coded_409_that_writes_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        state: str,
    ) -> None:
        sub_id, _ = await _seed(session, catalog, org_id, product_key="crm")
        await _force(admin_session, sub_id, state=state)
        target = catalog.add(make_version(product_key="crm", currency="INR"))
        before = await _counts(admin_session)

        response = await _add(client, org_id, target.id)
        assert response.status_code == 409, response.text
        assert _code(response) == domain.CODE_ALREADY_SUBSCRIBED
        if state == STATE_CANCELLED:
            assert "cannot be reopened" in response.json()["detail"]
        assert await _counts(admin_session) == before

    async def test_a_one_time_version_is_refused(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        once = catalog.add(
            make_version(product_key="crm", currency="INR", billing_interval="one_time")
        )
        before = await _counts(admin_session)
        for trial in (True, False):
            response = await _add(client, org_id, once.id, trial=trial)
            assert response.status_code == 422, response.text
            assert _code(response) == domain.CODE_ONE_TIME
        assert await _counts(admin_session) == before

    async def test_a_currency_the_organization_is_not_billed_in_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        await _seed(session, catalog, org_id, product_key="hrms", currency="INR")
        dollars = catalog.add(make_version(product_key="crm", currency="USD"))
        before = await _counts(admin_session)
        response = await _add(client, org_id, dollars.id)
        assert response.status_code == 422, response.text
        assert _code(response) == domain.CODE_ORG_CURRENCY_MISMATCH
        assert "INR" in response.json()["detail"]
        assert await _counts(admin_session) == before

        # The same currency is fine.
        rupees = catalog.add(make_version(product_key="crm", currency="INR"))
        assert (await _add(client, org_id, rupees.id)).status_code == 201

    @pytest.mark.parametrize("state", [STATE_PAST_DUE, STATE_SUSPENDED])
    async def test_a_past_due_or_suspended_subscription_still_sets_the_currency(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        state: str,
    ) -> None:
        sub_id, _ = await _seed(session, catalog, org_id, product_key="hrms", currency="INR")
        await _force(admin_session, sub_id, state=state)
        dollars = catalog.add(make_version(product_key="crm", currency="USD"))
        response = await _add(client, org_id, dollars.id)
        assert _code(response) == domain.CODE_ORG_CURRENCY_MISMATCH

    async def test_a_cancelled_subscription_does_not_set_the_currency(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, _ = await _seed(session, catalog, org_id, product_key="hrms", currency="INR")
        await _force(admin_session, sub_id, state=STATE_CANCELLED)
        dollars = catalog.add(make_version(product_key="crm", currency="USD"))
        assert (await _add(client, org_id, dollars.id)).status_code == 201

    async def test_the_currency_check_reads_only_the_named_organization(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """Org A is billed in INR; org B adding a USD product is org B's business alone."""
        await _seed(session, catalog, org_id, product_key="hrms", currency="INR")
        dollars = catalog.add(make_version(product_key="crm", currency="USD"))
        response = await _add(client, other_org, dollars.id)
        assert response.status_code == 201, response.text
        admin_session.expunge_all()
        rows = (
            (
                await admin_session.execute(
                    select(Subscription).where(Subscription.product_key == "crm")
                )
            )
            .scalars()
            .all()
        )
        assert [r.organization_id for r in rows] == [other_org]

    async def test_a_trial_is_the_versions_own_and_claims_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(
            make_version(product_key="crm", currency="INR", features={"trial_days": 21})
        )
        response = await _add(client, org_id, version.id, trial=True)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["state"] == STATE_TRIAL
        started = datetime.fromisoformat(body["current_period_start"])
        ends = datetime.fromisoformat(body["trial_ends_at"])
        assert ends - started == timedelta(days=21)
        admin_session.expunge_all()
        periods = (await admin_session.execute(select(BillingPeriod))).scalars().all()
        assert periods == [], "a trial owes nothing until it converts"
        (event,) = await _events(admin_session, org_id)
        assert event.event_type == domain.EVT_TRIAL_STARTED
        # The operator's words stay on the audit trail; the event is the customer org's.
        assert "note" not in event.payload
        (audit,) = (
            (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == domain.EVT_TRIAL_STARTED)
                )
            )
            .scalars()
            .all()
        )
        assert audit.reason == "Agreed on the onboarding call" and audit.acting_as == OPERATOR

    async def test_a_paid_add_claims_its_first_period_now(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(make_version(product_key="crm", currency="INR", amount_minor=99900))
        response = await _add(client, org_id, version.id, trial=False)
        assert response.status_code == 201, response.text
        assert response.json()["state"] == STATE_ACTIVE
        admin_session.expunge_all()
        (period,) = (await admin_session.execute(select(BillingPeriod))).scalars().all()
        assert period.kind == domain.KIND_OPENING and period.state == PERIOD_PENDING
        assert period.currency == "INR"

    async def test_a_trial_on_a_version_that_sells_none_is_a_coded_refusal(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(
            make_version(product_key="crm", currency="INR", features={"trial_days": 0})
        )
        before = await _counts(admin_session)
        response = await _add(client, org_id, version.id, trial=True)
        assert response.status_code == 422 and _code(response) == domain.CODE_TRIAL_NOT_OFFERED
        assert await _counts(admin_session) == before

    async def test_a_bundle_is_sold_on_this_path(
        self, client: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        duo = catalog.add(
            make_bundle(["hrms", "crm"], tier="duo", amount_minor=5000, currency="INR")
        )
        response = await _add(client, org_id, duo.id, product_key="suite", trial=True)
        assert response.status_code == 201, response.text
        assert response.json()["entitled_products"] == ["hrms", "crm"]

    async def test_a_lost_race_on_the_unique_index_is_the_same_coded_409(
        self,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two subscribes for one product that both pass the existence check: the loser hits the
        unique index, and is told what the check would have told it — not a 500."""
        from app import selectors

        await _seed(session, catalog, org_id, product_key="crm")
        version = catalog.add(make_version(product_key="crm", currency="INR"))

        async def _nothing(*_args: Any, **_kwargs: Any) -> None:
            return None

        monkeypatch.setattr(selectors, "get_by_product", _nothing)
        with pytest.raises(domain.CodedConflict) as caught:
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="crm", plan_version_id=version.id),
                )
        assert caught.value.code == domain.CODE_ALREADY_SUBSCRIBED

    async def test_the_customer_path_keeps_its_409_and_gains_the_code(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        await _seed(session, catalog, org_id, product_key="crm")
        version = catalog.add(make_version(product_key="crm", currency="INR"))
        with pytest.raises(domain.CodedConflict) as caught:
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="crm", plan_version_id=version.id),
                )
        assert caught.value.status_code == 409
        assert caught.value.code == domain.CODE_ALREADY_SUBSCRIBED


# =========================================================================== extend trial


def _extend_body(
    org_id: uuid.UUID, new_end: datetime, expected: datetime | None, **extra: Any
) -> dict[str, Any]:
    return {
        "organization_id": str(org_id),
        "operator_id": OPERATOR,
        "new_trial_ends_at": new_end.isoformat(),
        "expected_trial_ends_at": expected.isoformat() if expected else None,
        "note": "Customer is waiting on their payroll data import",
        **extra,
    }


async def _extend(
    client: AsyncClient,
    sub_id: uuid.UUID,
    org_id: uuid.UUID,
    new_end: datetime,
    expected: datetime | None,
    *,
    caller: str | None = "console",
    **extra: Any,
) -> Any:
    return await client.post(
        f"/internal/v1/subscriptions/{sub_id}/extend-trial",
        json=_extend_body(org_id, new_end, expected, **extra),
        headers={} if caller is None else _headers(caller),
    )


@dataclass(frozen=True)
class _Snapshot:
    """The trial as it stood before the request — plain values, so a request that rolls back (and
    expires the session's instances) cannot turn a later assertion into a lazy load."""

    trial_ends_at: datetime | None
    current_period_start: datetime | None
    plan_version_id: uuid.UUID
    currency: str


async def _trial(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
) -> tuple[uuid.UUID, _Snapshot]:
    sub_id, _ = await _seed(session, catalog, org_id, trial=True)
    sub = await _read(session, sub_id)
    snapshot = _Snapshot(
        trial_ends_at=sub.trial_ends_at,
        current_period_start=sub.current_period_start,
        plan_version_id=sub.plan_version_id,
        currency=sub.currency,
    )
    session.expunge_all()
    return sub_id, snapshot


class TestExtendTrialAuthorization:
    @pytest.mark.parametrize("caller", [None, "tests", "billing", "catalog"])
    async def test_every_caller_but_console_is_refused_and_writes_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        caller: str | None,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None
        before = await _counts(admin_session)
        response = await _extend(
            client,
            sub_id,
            org_id,
            sub.trial_ends_at + timedelta(days=7),
            sub.trial_ends_at,
            caller=caller,
        )
        assert response.status_code == 401, response.text
        assert (await _read(session, sub_id)).trial_ends_at == sub.trial_ends_at
        assert await _counts(admin_session) == before

    async def test_another_organizations_name_on_this_subscription_is_a_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None
        before = await _counts(admin_session)
        response = await _extend(
            client, sub_id, other_org, sub.trial_ends_at + timedelta(days=7), sub.trial_ends_at
        )
        assert response.status_code == 404, response.text
        assert (await _read(session, sub_id)).trial_ends_at == sub.trial_ends_at
        assert await _counts(admin_session) == before


class TestExtendTrial:
    async def test_it_moves_the_end_and_says_so_once_as_trial_extended(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        previous = sub.trial_ends_at
        assert previous is not None
        # Given in IST, as the operator surface sends it: stored and announced as the same instant.
        new_end = (previous + timedelta(days=10)).astimezone(IST)
        response = await _extend(client, sub_id, org_id, new_end, previous)
        assert response.status_code == 200, response.text
        assert datetime.fromisoformat(response.json()["trial_ends_at"]) == new_end
        assert response.json()["state"] == STATE_TRIAL

        live = await _read(session, sub_id)
        assert live.trial_ends_at == new_end and live.state == STATE_TRIAL

        admin_session.expunge_all()
        (transition,) = (
            (
                await admin_session.execute(
                    select(SubscriptionTransition).where(
                        SubscriptionTransition.reason == domain.REASON_TRIAL_EXTENDED
                    )
                )
            )
            .scalars()
            .all()
        )
        assert (transition.from_state, transition.to_state) == (STATE_TRIAL, STATE_TRIAL)
        assert transition.actor == {"type": "operator", "id": OPERATOR}

        events = await _events(admin_session, org_id)
        assert [e.event_type for e in events] == [
            domain.EVT_TRIAL_STARTED,
            domain.EVT_TRIAL_EXTENDED,
        ], "an extension is never a second trial_started"
        payload = events[-1].payload
        assert datetime.fromisoformat(payload["trial_ends_at"]) == new_end
        assert datetime.fromisoformat(payload["previous_trial_ends_at"]) == previous
        assert payload["state"] == STATE_TRIAL
        assert payload["subscription_id"] == str(sub_id)
        assert payload["entitled_products"] == ["hrms"]
        # Staff's reason is on the audit row below, never on the customer org's event.
        assert "note" not in payload

        (audit,) = (
            (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == domain.EVT_TRIAL_EXTENDED)
                )
            )
            .scalars()
            .all()
        )
        assert audit.acting_as == OPERATOR
        assert audit.reason == "Customer is waiting on their payroll data import"
        assert datetime.fromisoformat(audit.before["trial_ends_at"]) == previous
        assert datetime.fromisoformat(audit.after["trial_ends_at"]) == new_end

    async def test_a_replay_adds_no_time_twice(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        previous = sub.trial_ends_at
        assert previous is not None
        new_end = previous + timedelta(days=10)
        assert (await _extend(client, sub_id, org_id, new_end, previous)).status_code == 200
        before = await _counts(admin_session)

        again = await _extend(client, sub_id, org_id, new_end, previous)
        assert again.status_code == 409 and _code(again) == domain.CODE_STATE_CHANGED
        assert (await _read(session, sub_id)).trial_ends_at == new_end
        assert await _counts(admin_session) == before

    async def test_a_stale_view_is_a_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None
        before = await _counts(admin_session)
        response = await _extend(
            client,
            sub_id,
            org_id,
            sub.trial_ends_at + timedelta(days=5),
            sub.trial_ends_at - timedelta(seconds=1),
        )
        assert response.status_code == 409 and _code(response) == domain.CODE_STATE_CHANGED
        assert await _counts(admin_session) == before

    @pytest.mark.parametrize("state", [STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED])
    async def test_only_a_trial_is_extended(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        state: str,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None
        await _force(admin_session, sub_id, state=state)
        before = await _counts(admin_session)
        response = await _extend(
            client, sub_id, org_id, sub.trial_ends_at + timedelta(days=5), sub.trial_ends_at
        )
        assert response.status_code == 422 and _code(response) == domain.CODE_NOT_TRIAL
        assert await _counts(admin_session) == before

    async def test_it_is_refused_while_the_run_holds_a_claim(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None
        admin_session.add(
            BillingPeriod(
                organization_id=org_id,
                subscription_id=sub_id,
                kind=domain.KIND_TRIAL_CONVERSION,
                period_start=sub.trial_ends_at,
                period_end=sub.trial_ends_at + timedelta(days=30),
                plan_version_id=sub.plan_version_id,
                currency=sub.currency,
                items=[],
                state=PERIOD_PENDING,
                attempts=1,
            )
        )
        await admin_session.commit()
        before = await _counts(admin_session)
        response = await _extend(
            client, sub_id, org_id, sub.trial_ends_at + timedelta(days=5), sub.trial_ends_at
        )
        assert response.status_code == 422 and _code(response) == domain.CODE_CLAIM_IN_FLIGHT
        assert (await _read(session, sub_id)).trial_ends_at == sub.trial_ends_at
        assert await _counts(admin_session) == before

    async def test_the_total_is_capped_at_ninety_days_from_the_start(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None and sub.current_period_start is not None
        cap = sub.current_period_start + timedelta(days=domain.MAX_TOTAL_TRIAL_DAYS)
        before = await _counts(admin_session)

        over = await _extend(client, sub_id, org_id, cap + timedelta(seconds=1), sub.trial_ends_at)
        assert over.status_code == 422 and _code(over) == domain.CODE_TRIAL_CAP
        assert await _counts(admin_session) == before

        exactly = await _extend(client, sub_id, org_id, cap, sub.trial_ends_at)
        assert exactly.status_code == 200, exactly.text
        assert (await _read(session, sub_id)).trial_ends_at == cap

    async def test_the_listing_states_the_cap_for_a_trial_only(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        trial_id, trial = await _trial(session, catalog, org_id)
        paid_id, _ = await _seed(session, catalog, org_id, product_key="crm")
        response = await client.get(
            f"/internal/v1/organizations/{org_id}/subscriptions", headers=_headers()
        )
        assert response.status_code == 200, response.text
        by_id = {row["id"]: row for row in response.json()}
        assert trial.current_period_start is not None
        assert datetime.fromisoformat(by_id[str(trial_id)]["trial_max_ends_at"]) == (
            trial.current_period_start + timedelta(days=domain.MAX_TOTAL_TRIAL_DAYS)
        )
        assert by_id[str(paid_id)]["trial_max_ends_at"] is None

    @pytest.mark.parametrize("offset", [timedelta(0), timedelta(days=-1)])
    async def test_an_end_no_later_than_the_current_one_is_not_an_extension(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        offset: timedelta,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None
        before = await _counts(admin_session)
        response = await _extend(
            client, sub_id, org_id, sub.trial_ends_at + offset, sub.trial_ends_at
        )
        assert response.status_code == 422 and _code(response) == domain.CODE_TRIAL_NOT_EXTENDED
        assert await _counts(admin_session) == before

    async def test_a_lapsed_unconverted_trial_can_be_extended_into_the_future_only(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """The trial ended while the run was off (nothing claimed it): an extension must land in
        the future, or it would leave the trial as ended as it was."""
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.current_period_start is not None
        lapsed = datetime.now(UTC) - timedelta(days=2)
        await _force(admin_session, sub_id, trial_ends_at=lapsed)
        still_past = await _extend(client, sub_id, org_id, lapsed + timedelta(days=1), lapsed)
        assert _code(still_past) == domain.CODE_TRIAL_NOT_EXTENDED
        future = datetime.now(UTC) + timedelta(days=5)
        ok = await _extend(client, sub_id, org_id, future, lapsed)
        assert ok.status_code == 200, ok.text
        live = await _read(session, sub_id)
        due = domain.due_period(
            state=live.state,
            trial_ends_at=live.trial_ends_at,
            current_period_start=live.current_period_start,
            current_period_end=live.current_period_end,
            billing_interval=live.billing_interval,
            now=datetime.now(UTC),
        )
        assert due is None, "the conversion is no longer due"

    @pytest.mark.parametrize(
        "patch",
        [
            {"note": ""},
            {"note": "   "},
            {"note": "x" * 501},
            {"note": None},
            {"new_trial_ends_at": "2030-01-01T00:00:00"},
            {"expected_trial_ends_at": "2030-01-01T00:00:00"},
            {"days": 7},
        ],
        ids=["empty", "blank", "501", "missing-note", "naive-new", "naive-expected", "extra"],
    )
    async def test_a_malformed_body_is_refused_before_anything(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        patch: dict[str, Any],
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None
        body = {**_extend_body(org_id, sub.trial_ends_at + timedelta(days=3), sub.trial_ends_at)}
        body.update(patch)
        if body.get("note") is None:
            body.pop("note")
        before = await _counts(admin_session)
        response = await client.post(
            f"/internal/v1/subscriptions/{sub_id}/extend-trial", json=body, headers=_headers()
        )
        assert response.status_code == 422, response.text
        assert await _counts(admin_session) == before

    async def test_a_reserved_word_as_the_note_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, sub = await _trial(session, catalog, org_id)
        assert sub.trial_ends_at is not None
        before = await _counts(admin_session)
        for word in ("trial_extended", "renew"):
            response = await _extend(
                client,
                sub_id,
                org_id,
                sub.trial_ends_at + timedelta(days=3),
                sub.trial_ends_at,
                note=word,
            )
            assert _code(response) == domain.CODE_RESERVED_REASON, word
        assert await _counts(admin_session) == before


# ================================================================================ domain


class TestTrialExtensionRule:
    START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

    def _refusal(self, new_end: datetime, *, current: datetime | None = None) -> Any:
        return domain.trial_extension_refusal(
            trial_started_at=self.START,
            current_trial_ends_at=current or self.START + timedelta(days=14),
            new_trial_ends_at=new_end,
            now=self.START + timedelta(days=3),
        )

    def test_up_to_the_cap_is_allowed_and_one_second_past_is_not(self) -> None:
        cap = self.START + timedelta(days=90)
        assert self._refusal(cap) is None
        refused = self._refusal(cap + timedelta(seconds=1))
        assert refused is not None and refused[0] == domain.CODE_TRIAL_CAP

    def test_the_new_end_must_be_later(self) -> None:
        current = self.START + timedelta(days=14)
        for new_end in (current, current - timedelta(days=1)):
            refused = self._refusal(new_end)
            assert refused is not None and refused[0] == domain.CODE_TRIAL_NOT_EXTENDED

    def test_no_recorded_start_is_refused_rather_than_guessed(self) -> None:
        refused = domain.trial_extension_refusal(
            trial_started_at=None,
            current_trial_ends_at=self.START + timedelta(days=14),
            new_trial_ends_at=self.START + timedelta(days=20),
            now=self.START,
        )
        assert refused is not None and refused[0] == domain.CODE_TRIAL_CAP

    def test_the_cap_is_ninety_days(self) -> None:
        assert domain.MAX_TOTAL_TRIAL_DAYS == 90
        assert domain.trial_cap_at(STATE_TRIAL, self.START) == self.START + timedelta(days=90)
        assert domain.trial_cap_at(STATE_ACTIVE, self.START) is None
        assert domain.trial_cap_at(STATE_TRIAL, None) is None

    def test_the_extension_event_is_its_own_and_only_on_a_trial_self_edge(self) -> None:
        decision = domain.decide(
            from_state=STATE_TRIAL, to_state=STATE_TRIAL, reason=domain.REASON_TRIAL_EXTENDED
        )
        assert decision.event_type == domain.EVT_TRIAL_EXTENDED
        assert domain.decide(from_state=STATE_TRIAL, to_state=STATE_TRIAL).event_type == (
            domain.EVT_TRIAL_STARTED
        )
        for from_state, to_state in (
            (STATE_TRIAL, STATE_ACTIVE),
            (STATE_ACTIVE, STATE_ACTIVE),
            (None, STATE_TRIAL),
        ):
            with pytest.raises(domain.IllegalTransition):
                domain.decide(
                    from_state=from_state, to_state=to_state, reason=domain.REASON_TRIAL_EXTENDED
                )

    def test_the_reason_is_reserved(self) -> None:
        assert domain.REASON_TRIAL_EXTENDED in domain.RESERVED_REASONS
