"""A stop with a lapsed pending change must reach the product and the console as a stop.

**The bug.** A deferred plan change whose boundary has passed is applied by the stop that finds it
(`apply_lapsed_change`), and applying it staged an `upgraded`/`downgraded` event; the stop's own
event (`cancelled`, `suspended`) was staged in the same transaction. Both carry the transaction's
start as `occurred_at`, and both consumers drop an event that does not advance what they already
reflect (`product.services.project_subscription`: `occurred_at <= watermark`;
`console.domain._is_stale`). The relay orders by `created_at` alone, so tied rows come out in no
defined order. When the change folded first, the stop was dropped — and a cancellation is terminal:
nothing ever repaired it, the product stayed entitled, and console counted an active subscription
for good.

**What these tests do.** Not assert transition rows — those were always right. They drive the real
stop paths with a lapsed pending change, take the events subscription really staged, put them on the
wire with `build_envelope` (the relay's own function), and fold them through product's and console's
real consumers, in subprocesses (`import app` is ambiguous across services, one process can only
hold one — see `approval/tests/test_cross_service_contract.py` for the pattern). What is asserted is
the *folded* state: the activation off, the console fold cancelled or suspended.

The pre-fix behaviour is reproduced in the same file (`TestTheBugBeforeTheFix`) by putting the old
announcing behaviour back; it must fold to the wrong state, which is what proves the tests above
would have failed against the old code.

There is no customer suspend path in this service (the customer surface offers cancel only), so
the stop paths are the three that call `apply_lapsed_change`: cancel, an operator's suspend (the
console's delegate over `/internal`) and the dunning sweep's suspend.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import domain, services
from app.config import settings
from app.models import AuditLog, OutboxEvent, SubscriptionItem
from platform_core.events import build_envelope
from platform_core.testing import cross_service_schema_lock, schema_lock_key
from tests.helpers import FakeCatalog
from tests.test_billing_run import _subscription, _transitions
from tests.test_billing_run_review import _dunning_suspend
from tests.test_billing_run_round2 import (
    _change,
    _ctx,
    _period_ended,
    _subscribe,
    _version,
)

pytestmark = pytest.mark.integration

SERVICES = Path(__file__).resolve().parents[2]
PRODUCT_DIR = SERVICES / "product"
CONSOLE_DIR = SERVICES / "console"

#: The schemas the folds write — named as literals because importing the services' models would
#: pull a second `app` into this process, which is the one thing this module exists to avoid.
FOLDED_SCHEMAS = ("product", "console")
HOLDER = "services/subscription/tests/test_stop_event_folds.py"

RESULT_MARKER = "FOLD_RESULT "

#: A stop, as the three paths that apply a lapsed change spell it: the event it stages, the product
#: activation and the console state it must end in.
Stop = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[None]]


def _wall() -> datetime:
    return datetime.now(UTC)


async def _cancel(session: AsyncSession, org_id: uuid.UUID, subscription_id: uuid.UUID) -> None:
    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.cancel(session, _ctx(org_id), sub)


async def _operator_suspend(
    session: AsyncSession, org_id: uuid.UUID, subscription_id: uuid.UUID
) -> None:
    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.suspend(session, sub, operator_id="ops")


async def _dunning_suspension(
    session: AsyncSession, org_id: uuid.UUID, subscription_id: uuid.UUID
) -> None:
    """The ladder exhausted: a charge failed five days ago (so the subscription is `past_due`) and
    its window closed an hour ago, after the period boundary the pending change was waiting for."""
    await _dunning_suspend(
        session,
        subscription_id,
        invoice_id=uuid.uuid4(),
        failed_at=_wall() - timedelta(days=5),
        grace_ends_at=_wall() - timedelta(hours=1),
    )


#: stop name -> (how to do it, the event it stages, the console state it ends in)
STOPS: dict[str, tuple[Stop, str, str]] = {
    "cancel": (_cancel, "commerce.subscription.cancelled", "cancelled"),
    "operator_suspend": (_operator_suspend, "commerce.subscription.suspended", "suspended"),
    "dunning_suspend": (_dunning_suspension, "commerce.subscription.suspended", "suspended"),
}


#: Where console is left when the announcement folds first and the stop is dropped.
STUCK_ON = {"cancel": "active", "operator_suspend": "active", "dunning_suspend": "past_due"}


# --------------------------------------------------------------------- the cross-service plumbing


def _fold(service_dir: Path, org_id: uuid.UUID, envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold `envelopes`, in order, through `service_dir`'s real consumer; return what it left."""
    result = subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell — a sibling
        # service's own test helper, the same trust boundary as importing its code would be)
        [sys.executable, "-m", "tests.fold_envelopes", "--org", str(org_id)],
        cwd=str(service_dir),
        input=json.dumps(envelopes),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{service_dir.name} fold exited {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    (line,) = [ln for ln in result.stdout.splitlines() if ln.startswith(RESULT_MARKER)]
    folded: dict[str, Any] = json.loads(line[len(RESULT_MARKER) :])
    return folded


@pytest_asyncio.fixture
async def folded_schemas(session: AsyncSession) -> AsyncIterator[None]:
    """Hold the cross-service lock on the schemas the folds write, for the whole test.

    The two services' own suites truncate those schemas before each of their tests, and
    `make test-services` runs the suites at the same time. Taken in ascending key order (the
    helper refuses any other, so two suites needing two locks cannot deadlock). Depends on
    `session` for the truncation of this service's own schema, as every test here does.
    """
    ordered = sorted(FOLDED_SCHEMAS, key=schema_lock_key)
    async with AsyncExitStack() as stack:
        for schema in ordered:
            await stack.enter_async_context(
                cross_service_schema_lock(settings().app_dsn, schema, holder=HOLDER)
            )
        yield


@pytest.fixture
def folded_orgs(org_id: uuid.UUID) -> list[uuid.UUID]:
    """The organizations a test folded events for, so the cleanup below finds every one."""
    return [org_id]


@pytest_asyncio.fixture
async def cleanup_folds(folded_schemas: None, folded_orgs: list[uuid.UUID]) -> AsyncIterator[None]:
    """Delete what the folds wrote for this test's organizations, before the lock is released."""
    yield
    engine = create_async_engine(settings().admin_dsn)
    async with engine.begin() as conn:
        # `processed_events` is keyed by event id, not by tenant: the ids this test minted are
        # its own, collide with nothing, and are cleared by the owning suite's next truncation.
        for table in (
            "product.activation_sources",
            "product.activations",
            "product.audit_logs",
            "product.outbox",
            "console.org_commercial_state",
            "console.revenue_events",
            "console.audit_logs",
            "console.outbox",
        ):
            await conn.execute(
                text(f"DELETE FROM {table} WHERE organization_id = ANY(:orgs)"),  # noqa: S608
                {"orgs": folded_orgs},
            )
    await engine.dispose()


def _as_another_org(
    envelopes: list[dict[str, Any]], folded_orgs: list[uuid.UUID]
) -> tuple[uuid.UUID, list[dict[str, Any]]]:
    """The same stream for a fresh organization, with fresh event ids: a second, independent fold.

    Both consumers remember the events they have processed, so folding the same stream twice in one
    organization would be a redelivery and a no-op, not a second ordering to compare.
    """
    other = uuid.uuid4()
    folded_orgs.append(other)
    copied = json.loads(json.dumps(envelopes))
    for envelope in copied:
        envelope["event_id"] = str(uuid.uuid4())
        envelope["organization_id"] = str(other)
        envelope["payload"]["organization_id"] = str(other)
    return other, copied


# --------------------------------------------------------------------- reading what was staged


async def _staged(admin_session: AsyncSession, subscription_id: uuid.UUID) -> list[OutboxEvent]:
    """Every event the subscription staged, in the order they were staged: `created_at` is the
    transaction's start, so rows of one transaction tie, and the heap position breaks the tie the
    way an unordered relay read usually (but not reliably) would."""
    admin_session.expunge_all()
    async with admin_session.begin():
        rows = (
            (
                await admin_session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.aggregate_id == subscription_id)
                    .order_by(OutboxEvent.created_at, text("outbox.ctid"))
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


def _envelopes(rows: list[OutboxEvent]) -> list[dict[str, Any]]:
    """The wire envelopes, built by the relay's own function — JSON-ready as they stand."""
    return [build_envelope(row, producer="subscription") for row in rows]


async def _audits(admin_session: AsyncSession, subscription_id: uuid.UUID) -> list[str]:
    admin_session.expunge_all()
    async with admin_session.begin():
        rows = (
            await admin_session.execute(
                select(AuditLog.action).where(AuditLog.entity_id == subscription_id)
            )
        ).scalars()
        return list(rows.all())


async def _lapsed_downgrade(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
) -> tuple[uuid.UUID, Any]:
    """A subscription on a dearer plan with a downgrade waiting — and the run behind: its period
    ended a day ago and nothing rolled it, which is what `BILLING_RUN_ENABLED=false` looks like."""
    subscription_id, _ = await _subscribe(session, catalog, org_id, 200000)
    low = await _version(catalog, 100000)
    await _change(session, catalog, org_id, subscription_id, low.id)
    await _period_ended(subscription_id)
    sub = await _subscription(session, subscription_id)
    assert sub.pending_change is not None, "the downgrade must be waiting"
    return subscription_id, low


def _assert_off(product: dict[str, Any], console: dict[str, Any], console_state: str) -> None:
    activation = product["activations"]["hrms"]
    assert activation["entitles"] is False, (
        f"the product stayed entitled after the stop: {product['activations']}"
    )
    assert activation["status"] == "suspended"
    assert "hrms" not in product["entitlements"]
    assert console["states"]["hrms"] == console_state, (
        f"console folded {console['states']['hrms']!r}, not {console_state!r}"
    )
    assert "hrms" not in console["active_products"]
    assert console["mrr_minor"] == 0
    assert console["locked"] is (console_state == "suspended")


# --------------------------------------------------------------------- the fix


@pytest.mark.usefixtures("cleanup_folds")
class TestAStopWithALapsedChangeFoldsAsAStop:
    @pytest.mark.parametrize("stop", list(STOPS))
    async def test_the_product_and_console_end_stopped(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        stop: str,
    ) -> None:
        do_stop, stop_event, console_state = STOPS[stop]
        subscription_id, low = await _lapsed_downgrade(session, catalog, org_id)

        await do_stop(session, org_id, subscription_id)

        staged = await _staged(admin_session, subscription_id)
        envelopes = _envelopes(staged)
        _assert_off(
            _fold(PRODUCT_DIR, org_id, envelopes),
            _fold(CONSOLE_DIR, org_id, envelopes),
            console_state,
        )
        # The stop is the last thing said, and it says the final pin.
        assert staged[-1].event_type == stop_event
        assert staged[-1].payload["plan_version_id"] == str(low.id)

    @pytest.mark.parametrize("stop", list(STOPS))
    async def test_no_two_events_tie_so_no_order_can_drop_the_stop(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        stop: str,
    ) -> None:
        """The property the consumers' guards need: each event advances the one before it. The
        stop's transaction staged exactly one event, and the change it applied is on the record as
        a transition and an audit row rather than as an announcement."""
        do_stop, stop_event, _ = STOPS[stop]
        subscription_id, low = await _lapsed_downgrade(session, catalog, org_id)

        await do_stop(session, org_id, subscription_id)

        staged = await _staged(admin_session, subscription_id)
        types = [e.event_type for e in staged]
        assert domain.EVT_DOWNGRADED not in types and domain.EVT_UPGRADED not in types, types
        assert len({e.created_at for e in staged}) == len(staged), (
            f"two events share an occurred_at, so a consumer may drop one: {types}"
        )
        assert types.count(stop_event) == 1 and types[-1] == stop_event

        assert domain.REASON_DOWNGRADE in await _transitions(session, subscription_id)
        assert domain.EVT_DOWNGRADED in await _audits(admin_session, subscription_id)
        sub = await _subscription(session, subscription_id)
        assert sub.plan_version_id == low.id
        assert sub.pending_change is None and sub.pending_plan_version_id is None

    @pytest.mark.parametrize("stop", list(STOPS))
    async def test_the_outcome_does_not_depend_on_which_event_a_consumer_sees_first(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        stop: str,
    ) -> None:
        """Delivery order across relay replicas is not defined. With one event per instant, the
        stream folded newest-first lands where oldest-first does: every older event is dropped
        behind the stop, and none of them is the old failure."""
        do_stop, _, console_state = STOPS[stop]
        subscription_id, _ = await _lapsed_downgrade(session, catalog, org_id)
        await do_stop(session, org_id, subscription_id)

        envelopes = _envelopes(await _staged(admin_session, subscription_id))
        _assert_off(
            _fold(PRODUCT_DIR, org_id, list(reversed(envelopes))),
            _fold(CONSOLE_DIR, org_id, list(reversed(envelopes))),
            console_state,
        )

    async def test_a_stop_with_nothing_lapsed_is_unchanged(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """No pending change: nothing applied, announced or silenced; the stop folds as ever."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 200000)
        await _cancel(session, org_id, subscription_id)

        staged = await _staged(admin_session, subscription_id)
        assert [e.event_type for e in staged] == [
            "commerce.subscription.activated",
            "commerce.subscription.cancelled",
        ]
        envelopes = _envelopes(staged)
        _assert_off(
            _fold(PRODUCT_DIR, org_id, envelopes),
            _fold(CONSOLE_DIR, org_id, envelopes),
            "cancelled",
        )

    async def test_a_pending_change_that_has_not_lapsed_is_dropped_by_the_cancel_unannounced(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """A change still waiting for a boundary in the future is never applied by a cancel; it is
        cleared, as before, and the cancel is the only event."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 200000)
        low = await _version(catalog, 100000)
        await _change(session, catalog, org_id, subscription_id, low.id)

        await _cancel(session, org_id, subscription_id)

        staged = await _staged(admin_session, subscription_id)
        assert [e.event_type for e in staged][-1] == "commerce.subscription.cancelled"
        assert domain.EVT_DOWNGRADED not in [e.event_type for e in staged]
        sub = await _subscription(session, subscription_id)
        assert sub.pending_change is None


@pytest.mark.usefixtures("cleanup_folds")
class TestTheBugBeforeTheFix:
    """The old behaviour, put back: apply the lapsed change *announcing* it, as the code did.

    This is the evidence that the tests above can fail. With the announcement restored, the
    transaction stages a `downgraded` and a `cancelled` at one instant, and a consumer that folds
    the first has nothing to advance for the second.
    """

    @pytest.fixture(autouse=True)
    def announcing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = services.apply_pending_change

        async def announce(
            session: AsyncSession, subscription: Any, *, boundary: datetime, silent: bool = False
        ) -> bool:
            return await real(session, subscription, boundary=boundary, silent=False)

        monkeypatch.setattr(services, "apply_pending_change", announce)

    @pytest.mark.parametrize("stop", list(STOPS))
    async def test_the_old_events_tie_and_the_first_one_folded_wins(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        folded_orgs: list[uuid.UUID],
        stop: str,
    ) -> None:
        do_stop, stop_event, _ = STOPS[stop]
        subscription_id, _ = await _lapsed_downgrade(session, catalog, org_id)

        await do_stop(session, org_id, subscription_id)

        staged = await _staged(admin_session, subscription_id)
        types = [e.event_type for e in staged]
        assert types[-2:] == [domain.EVT_DOWNGRADED, stop_event], types
        assert staged[-2].created_at == staged[-1].created_at, "the pair ties on occurred_at"

        # The announcement folded first (the order the rows were staged in, which is the order an
        # unordered relay read usually returns): the stop is dropped as stale, and the product is
        # still entitled — the state nothing ever repairs.
        envelopes = _envelopes(staged)
        product = _fold(PRODUCT_DIR, org_id, envelopes)
        console = _fold(CONSOLE_DIR, org_id, envelopes)
        assert product["activations"]["hrms"]["entitles"] is True, (
            "the stop was folded: the pre-fix behaviour is no longer reproducible, so the "
            "regression tests above prove nothing"
        )
        assert "hrms" in product["entitlements"]
        # Console is stuck on the announcement's state, whatever it was: `active` for a cancel and
        # an operator's suspension, `past_due` for the ladder's: never locked, never cancelled.
        assert console["states"]["hrms"] == STUCK_ON[stop]
        assert console["locked"] is False

        # Whereas folding the stop first would have been harmless — which is why it went unseen.
        other, swapped = _as_another_org(list(reversed(envelopes)), folded_orgs)
        reversed_product = _fold(PRODUCT_DIR, other, swapped)
        assert reversed_product["activations"]["hrms"]["entitles"] is False


# --------------------------------------------------------------------- what must not change


class TestTheBillingRunStillAnnouncesItsChange:
    async def test_the_renewal_boundary_application_emits_its_event(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Silence is for a stop only. The run's own application of a waiting change, at the
        boundary it claims the renewal for, is a transition nobody else announces: `downgraded`
        is staged exactly as it was, in the claim's transaction, with the stop nowhere near."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 200000)
        low = await _version(catalog, 100000)
        await _change(session, catalog, org_id, subscription_id, low.id)

        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            assert sub.current_period_end is not None
            applied = await services.apply_pending_change(
                session, sub, boundary=sub.current_period_end
            )
        assert applied is True

        types = [e.event_type for e in await _staged(admin_session, subscription_id)]
        assert types.count(domain.EVT_DOWNGRADED) == 1, types

    async def test_a_silent_application_resolves_the_change_in_full(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """`silent` omits the event and nothing else: pin, items, pending state, the transition
        row and the audit row are exactly what the announcing path writes."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 200000)
        low = await _version(catalog, 100000)
        await _change(session, catalog, org_id, subscription_id, low.id)
        before = len(await _staged(admin_session, subscription_id))

        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            assert sub.current_period_end is not None
            await services.apply_pending_change(
                session, sub, boundary=sub.current_period_end, silent=True
            )

        assert len(await _staged(admin_session, subscription_id)) == before
        sub = await _subscription(session, subscription_id)
        assert sub.plan_version_id == low.id
        assert sub.pending_change is None and sub.pending_plan_version_id is None
        async with session.begin():
            prices = (
                await session.execute(
                    select(SubscriptionItem.price_id).where(
                        SubscriptionItem.subscription_id == subscription_id
                    )
                )
            ).scalars()
            assert list(prices.all()) == [low.prices[0].id]
        assert domain.REASON_DOWNGRADE in await _transitions(session, subscription_id)
        assert domain.EVT_DOWNGRADED in await _audits(admin_session, subscription_id)
