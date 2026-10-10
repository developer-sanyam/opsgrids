"""The load-bearing behaviours of the billing service, against real PostgreSQL.

Each property here is one a customer dispute or an audit would eventually test for us, so each
is proven now, with its negative case:

* **The money foots and never floats.** An invoice's lines sum exactly to its total (largest-
  remainder, `Money.allocate`); a line in the wrong currency is refused (`Money._same`).
* **The financial record is immutable below the application.** A finalized invoice cannot be
  altered (freeze trigger); invoice lines and credit-ledger rows cannot be updated or deleted
  (privilege revoke). A correction is a new row, never an edit.
* **The credit balance is a windowed SUM, never a stored column.** Grant, consume and reversal
  are rows; a lapsed grant drops out of the balance on its own, proven by querying two instants.
* **Isolation and authorization hold.** One tenant cannot see another's invoices or balance;
  every read route needs its permission; the internal write API needs a service token.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors, services
from app.config import settings
from app.main import app
from app.models import (
    INVOICE_FINALIZED,
    INVOICE_VOID,
    CreditLedgerEntry,
    Invoice,
    OutboxEvent,
)
from app.schemas import (
    CreditConsumeIn,
    CreditGrantIn,
    CreditReverseIn,
    FinalizeInvoiceIn,
    FinalizeLineIn,
    VoidInvoiceIn,
)
from platform_core.auth import Principal, get_principal
from platform_core.errors import BusinessRuleProblem
from platform_core.money import CurrencyMismatch, Money
from tests.helpers import CREDIT_READ, INVOICE_READ, customer_principal, service_token_headers

pytestmark = pytest.mark.integration

_PERIOD_START = datetime(2026, 1, 1, tzinfo=UTC)
_PERIOD_END = datetime(2026, 2, 1, tzinfo=UTC)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _finalize_payload(
    org_id: uuid.UUID,
    *,
    number: str = "INV-0001",
    lines: list[FinalizeLineIn] | None = None,
    currency: str = "USD",
    expected_total_minor: int | None = None,
) -> FinalizeInvoiceIn:
    return FinalizeInvoiceIn(
        organization_id=org_id,
        subscription_id=uuid.uuid4(),
        number=number,
        currency=currency,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        lines=lines
        or [
            FinalizeLineIn(description="Platform fee", quantity=Decimal(1), amount_minor=1000),
            FinalizeLineIn(description="Seats", quantity=Decimal(3), amount_minor=1500),
        ],
        expected_total_minor=expected_total_minor,
    )


async def _finalize(session: AsyncSession, payload: FinalizeInvoiceIn) -> Invoice:
    async with session.begin():
        invoice = await services.finalize_invoice(session, payload, request=None)
        return await services.require_invoice(
            session, invoice.id, organization_id=payload.organization_id
        )


# --------------------------------------------------------------------- money mechanics (pure)


class TestMoneyMechanics:
    def test_lines_foot_to_total_exactly(self) -> None:
        lines = [
            domain.LineDraft("a", Decimal(1), 1000, "USD"),
            domain.LineDraft("b", Decimal(1), 333, "USD"),
        ]
        assert domain.sum_lines(lines, "USD") == Money(1333, "USD")
        # The invariant a finalized invoice asserts — no exception when it foots.
        domain.assert_foots(Money(1333, "USD"), lines)

    def test_a_line_that_does_not_foot_is_refused(self) -> None:
        lines = [domain.LineDraft("a", Decimal(1), 1000, "USD")]
        with pytest.raises(domain.LinesDoNotFoot):
            domain.assert_foots(Money(999, "USD"), lines)

    def test_a_currency_mismatch_between_lines_raises(self) -> None:
        lines = [
            domain.LineDraft("usd", Decimal(1), 1000, "USD"),
            domain.LineDraft("eur", Decimal(1), 1000, "EUR"),
        ]
        with pytest.raises(CurrencyMismatch):
            domain.sum_lines(lines, "USD")

    def test_proration_parts_sum_back_to_the_whole(self) -> None:
        full = Money(1000, "USD")
        earned = domain.prorate(full, elapsed_units=10, period_units=30)
        # Largest-remainder: 10/30 of 1000 rounds to 333, and the parts still foot to 1000.
        assert earned == Money(333, "USD")
        unused = domain.prorate(full, elapsed_units=20, period_units=30)
        assert earned + unused == full

    def test_a_discount_allocates_without_losing_a_unit(self) -> None:
        parts = domain.allocate_discount(Money(100, "USD"), [1, 1, 1])
        assert sum(p.minor for p in parts) == 100  # 34 + 33 + 33, never 33+33+33=99


# --------------------------------------------------------------------- finalize


class TestFinalize:
    async def test_finalize_creates_a_finalized_invoice_whose_lines_foot(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice = await _finalize(session, _finalize_payload(org_id))
        assert invoice.state == INVOICE_FINALIZED
        assert invoice.finalized_at is not None
        assert invoice.total_minor == 2500
        assert sum(line.amount_minor for line in invoice.lines) == invoice.total_minor

    async def test_a_wrong_currency_line_is_rejected(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        payload = _finalize_payload(
            org_id,
            lines=[
                FinalizeLineIn(description="usd", quantity=Decimal(1), amount_minor=1000),
                FinalizeLineIn(
                    description="eur", quantity=Decimal(1), amount_minor=500, currency="EUR"
                ),
            ],
        )
        with pytest.raises(BusinessRuleProblem, match="currency"):
            await _finalize(session, payload)

    async def test_a_stated_total_that_does_not_foot_is_rejected(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        payload = _finalize_payload(org_id, expected_total_minor=9999)
        with pytest.raises(BusinessRuleProblem):
            await _finalize(session, payload)

    async def test_finalize_stages_the_finalized_event(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice = await _finalize(session, _finalize_payload(org_id))
        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.event_type == "commerce.invoice.finalized",
                        OutboxEvent.aggregate_id == invoice.id,
                    )
                )
            ).scalar_one()
        assert count == 1


# --------------------------------------------------------------------- freeze at the DB


class TestFinalizedInvoiceIsFrozen:
    async def test_a_raw_update_of_a_finalized_invoice_raises(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The net under the service guard: even a direct UPDATE, bypassing every check, is
        refused by the freeze trigger."""
        invoice = await _finalize(session, _finalize_payload(org_id))
        with pytest.raises(IntegrityError, match="immutable"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE billing.invoices SET total_minor = 1 WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(invoice.id)},
                )

    async def test_voiding_is_the_one_allowed_transition(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice = await _finalize(session, _finalize_payload(org_id))
        async with session.begin():
            fresh = await services.require_invoice(session, invoice.id, organization_id=org_id)
            voided = await services.void_invoice(
                session, fresh, VoidInvoiceIn(organization_id=org_id, reason="billed in error")
            )
        assert voided.state == INVOICE_VOID
        assert voided.voided_at is not None
        # The money and identity are untouched — voiding marks, it does not rewrite.
        assert voided.total_minor == 2500

    async def test_void_stages_the_voided_event(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice = await _finalize(session, _finalize_payload(org_id))
        async with session.begin():
            fresh = await services.require_invoice(session, invoice.id, organization_id=org_id)
            await services.void_invoice(
                session, fresh, VoidInvoiceIn(organization_id=org_id, reason="dup")
            )
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.event_type == "commerce.invoice.voided",
                        OutboxEvent.aggregate_id == invoice.id,
                    )
                )
            ).scalar_one()
        assert count == 1

    async def test_a_voided_invoice_cannot_be_refinalized(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Void is terminal: the trigger refuses any change to an already-void invoice."""
        invoice = await _finalize(session, _finalize_payload(org_id))
        async with session.begin():
            fresh = await services.require_invoice(session, invoice.id, organization_id=org_id)
            await services.void_invoice(
                session, fresh, VoidInvoiceIn(organization_id=org_id, reason="x")
            )
        with pytest.raises(IntegrityError, match="immutable"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE billing.invoices SET state = 'finalized' "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(invoice.id)},
                )

    async def test_the_service_layer_refuses_to_void_twice(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice = await _finalize(session, _finalize_payload(org_id))
        async with session.begin():
            fresh = await services.require_invoice(session, invoice.id, organization_id=org_id)
            await services.void_invoice(
                session, fresh, VoidInvoiceIn(organization_id=org_id, reason="once")
            )
        with pytest.raises(BusinessRuleProblem, match="void"):
            async with session.begin():
                again = await services.require_invoice(session, invoice.id, organization_id=org_id)
                await services.void_invoice(
                    session, again, VoidInvoiceIn(organization_id=org_id, reason="twice")
                )

    async def test_a_late_line_insert_under_a_finalized_invoice_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The insert-shaped hole next to the freeze trigger: the legitimate flow writes an
        invoice and its lines in ONE transaction (`_finalize` above, which succeeded), so a raw
        INSERT of a further line in a LATER transaction is exactly the corruption the
        `trg_invoice_lines_no_late_insert` trigger refuses — a frozen total that no longer
        foots."""
        invoice = await _finalize(session, _finalize_payload(org_id))
        invoice_id = invoice.id  # read before the failed transaction expires the object
        with pytest.raises(IntegrityError, match="frozen"):
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO billing.invoice_lines "
                        "(id, organization_id, invoice_id, description, quantity, amount_minor) "
                        "VALUES (gen_random_uuid(), :org, CAST(:inv AS uuid), 'smuggled', 1, 100)"
                    ),
                    {"org": str(org_id), "inv": str(invoice_id)},
                )

        # The refusal left the financial record intact: the lines still foot to the total.
        async with session.begin():
            fresh = await services.require_invoice(session, invoice_id, organization_id=org_id)
            assert sum(line.amount_minor for line in fresh.lines) == fresh.total_minor
            assert len(fresh.lines) == 2


# --------------------------------------------------------------------- append-only at the DB


class TestAppendOnly:
    async def test_updating_an_invoice_line_is_denied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The app role holds no UPDATE on invoice_lines — a correction is a new invoice."""
        invoice = await _finalize(session, _finalize_payload(org_id))
        line_id = invoice.lines[0].id
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE billing.invoice_lines SET amount_minor = 0 "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(line_id)},
                )

    async def test_deleting_an_invoice_line_is_denied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice = await _finalize(session, _finalize_payload(org_id))
        line_id = invoice.lines[0].id
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with session.begin():
                await session.execute(
                    text("DELETE FROM billing.invoice_lines WHERE id = CAST(:id AS uuid)"),
                    {"id": str(line_id)},
                )

    async def test_updating_a_credit_entry_is_denied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            entry = await services.grant_credit(
                session,
                CreditGrantIn(
                    organization_id=org_id, amount_minor=1000, currency="USD", reason="comp"
                ),
                request=None,
            )
            entry_id = entry.id
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE billing.credit_ledger SET amount_minor = 0 "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(entry_id)},
                )

    async def test_deleting_an_invoice_is_denied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice = await _finalize(session, _finalize_payload(org_id))
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with session.begin():
                await session.execute(
                    text("DELETE FROM billing.invoices WHERE id = CAST(:id AS uuid)"),
                    {"id": str(invoice.id)},
                )


# --------------------------------------------------------------------- balance = SUM


class TestCreditBalanceIsAWindowedSum:
    async def test_grant_consume_and_reversal_are_all_rows_in_the_sum(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            await services.grant_credit(
                session,
                CreditGrantIn(
                    organization_id=org_id, amount_minor=1000, currency="USD", reason="grant"
                ),
                request=None,
            )
            consumed = await services.consume_credit(
                session,
                CreditConsumeIn(
                    organization_id=org_id, amount_minor=300, currency="USD", reason="spend"
                ),
                request=None,
            )
        async with session.begin():
            after_consume = await selectors.credit_balance(
                session, organization_id=org_id, currency="USD", at=datetime.now(UTC)
            )
        assert after_consume == 700, "the consumption is a negative row in the SUM"

        async with session.begin():
            await services.reverse_credit(
                session,
                CreditReverseIn(
                    organization_id=org_id, reversal_of=consumed.id, reason="refund the spend"
                ),
                request=None,
            )
        async with session.begin():
            after_reversal = await selectors.credit_balance(
                session, organization_id=org_id, currency="USD", at=datetime.now(UTC)
            )
        assert after_reversal == 1000, "the reversal is another row, restoring the balance"

    async def test_a_lapsed_grant_drops_out_of_the_balance_automatically(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The headline of ADR-0004 §3: balance is a SUM over the covering rows, so a grant whose
        window has closed is simply not counted — nothing runs to expire it. Proven by asking at
        two instants, before and after the window closes."""
        window_start = datetime(2026, 1, 1, tzinfo=UTC)
        window_end = datetime(2026, 6, 1, tzinfo=UTC)
        async with session.begin():
            await services.grant_credit(
                session,
                CreditGrantIn(
                    organization_id=org_id,
                    amount_minor=1000,
                    currency="USD",
                    reason="three months comp",
                    valid_from=window_start,
                    valid_until=window_end,
                ),
                request=None,
            )

        async with session.begin():
            during = await selectors.credit_balance(
                session, organization_id=org_id, currency="USD", at=datetime(2026, 3, 1, tzinfo=UTC)
            )
            after = await selectors.credit_balance(
                session, organization_id=org_id, currency="USD", at=datetime(2026, 9, 1, tzinfo=UTC)
            )
        assert during == 1000, "inside its window the grant counts"
        assert after == 0, "past its window the grant drops out of the SUM on its own"


# --------------------------------------------------------------------- reversal is exactly once


class TestReversalNetsToZeroAndHappensOnce:
    async def test_reversing_a_time_boxed_grant_nets_to_zero_at_every_instant(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The reversal inherits the ORIGINAL entry's window, so a reversed pair sums to zero at
        any instant the balance can be asked about. The old bug windowed the reversal
        `[now, NULL)`: once the grant's `valid_until` passed, the grant left the SUM while the
        open-ended reversal kept counting — the balance read -amount forever after."""
        now = datetime.now(UTC)
        window_start = now - timedelta(hours=1)
        window_end = now + timedelta(hours=1)
        async with session.begin():
            grant = await services.grant_credit(
                session,
                CreditGrantIn(
                    organization_id=org_id,
                    amount_minor=1000,
                    currency="USD",
                    reason="time-boxed comp",
                    valid_from=window_start,
                    valid_until=window_end,
                ),
                request=None,
            )
        async with session.begin():
            reversal = await services.reverse_credit(
                session,
                CreditReverseIn(
                    organization_id=org_id, reversal_of=grant.id, reason="granted in error"
                ),
                request=None,
            )
        assert reversal.valid_from == window_start, "the reversal inherits the grant's window"
        assert reversal.valid_until == window_end

        async with session.begin():
            during = await selectors.credit_balance(
                session, organization_id=org_id, currency="USD", at=now
            )
            after = await selectors.credit_balance(
                session, organization_id=org_id, currency="USD", at=window_end + timedelta(hours=1)
            )
        assert during == 0, "inside the window the pair nets to zero"
        assert after == 0, "past the window BOTH rows drop out — never -1000"

    async def test_reversing_the_same_entry_twice_returns_the_one_reversal(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Idempotent under at-least-once delivery: a retried reverse returns the reversal the
        first attempt wrote — same row, same id — and the balance moves exactly once."""
        async with session.begin():
            grant = await services.grant_credit(
                session,
                CreditGrantIn(
                    organization_id=org_id, amount_minor=1000, currency="USD", reason="comp"
                ),
                request=None,
            )
        async with session.begin():
            first = await services.reverse_credit(
                session,
                CreditReverseIn(organization_id=org_id, reversal_of=grant.id, reason="undo"),
                request=None,
            )
        async with session.begin():
            second = await services.reverse_credit(
                session,
                CreditReverseIn(
                    organization_id=org_id, reversal_of=grant.id, reason="undo, retried"
                ),
                request=None,
            )
        assert second.id == first.id, "a retry returns the existing reversal, not a new row"

        async with session.begin():
            balance = await selectors.credit_balance(
                session, organization_id=org_id, currency="USD", at=datetime.now(UTC)
            )
            reversals = (
                await session.execute(
                    select(func.count())
                    .select_from(CreditLedgerEntry)
                    .where(CreditLedgerEntry.reversal_of == grant.id)
                )
            ).scalar_one()
        assert balance == 0, "the balance moved once, not once per retry"
        assert reversals == 1

    async def test_a_second_reversal_row_is_refused_by_the_database(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The net under the service's idempotency check: a raw INSERT naming the same
        `reversal_of` — the write of a racing transaction or a stray script, which the pre-check
        cannot see — is refused by the partial-unique `ix_credit_ledger_org_reversal_of`."""
        async with session.begin():
            grant = await services.grant_credit(
                session,
                CreditGrantIn(
                    organization_id=org_id, amount_minor=1000, currency="USD", reason="comp"
                ),
                request=None,
            )
        async with session.begin():
            await services.reverse_credit(
                session,
                CreditReverseIn(organization_id=org_id, reversal_of=grant.id, reason="undo"),
                request=None,
            )
        with pytest.raises(IntegrityError, match="ix_credit_ledger_org_reversal_of"):
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO billing.credit_ledger "
                        "(id, organization_id, entry_type, amount_minor, currency, reason, "
                        " valid_from, reversal_of) "
                        "VALUES (gen_random_uuid(), :org, 'reversal', -1000, 'USD', "
                        "'rogue second reversal', now(), CAST(:rev AS uuid))"
                    ),
                    {"org": str(org_id), "rev": str(grant.id)},
                )


# --------------------------------------------------------------------- isolation


class TestCrossTenantIsolation:
    async def test_one_tenant_cannot_see_anothers_invoice_or_balance(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """Row-level security, asserted rather than assumed. Org B's rows are seeded on a
        BYPASSRLS session; the app session bound to org A must see none of them."""
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "INSERT INTO billing.invoices "
                    "(id, organization_id, number, state, currency, total_minor, "
                    " period_start, period_end, finalized_at, created_at, updated_at) "
                    "VALUES (:id, :org, 'B-1', 'finalized', 'USD', 5000, :ps, :pe, now(), "
                    " now(), now())"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org": str(org_b),
                    "ps": _PERIOD_START,
                    "pe": _PERIOD_END,
                },
            )
            await admin_session.execute(
                text(
                    "INSERT INTO billing.credit_ledger "
                    "(id, organization_id, entry_type, amount_minor, currency, reason, "
                    " valid_from, created_at, updated_at) "
                    "VALUES (:id, :org, 'grant', 9999, 'USD', 'b comp', now(), now(), now())"
                ),
                {"id": str(uuid.uuid4()), "org": str(org_b)},
            )

        # Org A finalizes its own invoice, then reads: it sees exactly its own.
        my_invoice = await _finalize(session, _finalize_payload(org_id, number="A-1"))
        async with session.begin():
            ctx = _read_ctx(org_id)
            visible = await selectors.list_invoices(session, ctx)
            balance = await selectors.credit_balance(
                session, organization_id=org_id, currency="USD", at=datetime.now(UTC)
            )

        ids = {inv.id for inv in visible}
        assert my_invoice.id in ids
        assert all(inv.organization_id == org_id for inv in visible)
        assert balance == 0, "org B's 9999 credit must not appear in org A's balance"

    async def test_an_invoice_lookup_filters_its_tenant_not_rls_alone(
        self,
        client,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """Org B's invoice, asked for by id on behalf of org A. On a BYPASSRLS session only the
        explicit predicate stands between them; over HTTP the tenant read and the internal void
        both answer 404 and leave the invoice as it was."""
        theirs = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "INSERT INTO billing.invoices "
                    "(id, organization_id, number, state, currency, total_minor, "
                    " period_start, period_end, finalized_at, created_at, updated_at) "
                    "VALUES (:id, :org, 'B-1', 'finalized', 'USD', 5000, :ps, :pe, now(), "
                    " now(), now())"
                ),
                {"id": str(theirs), "org": str(org_b), "ps": _PERIOD_START, "pe": _PERIOD_END},
            )

        async with admin_session.begin():
            assert (
                await selectors.get_invoice(admin_session, theirs, organization_id=org_id) is None
            )
            found = await selectors.get_invoice(admin_session, theirs, organization_id=org_b)
            assert found is not None and found.id == theirs

        with as_principal(customer_principal(org_id, perms=[INVOICE_READ])):
            assert (await client.get(f"/api/v1/invoices/{theirs}")).status_code == 404

        resp = await client.post(
            f"/internal/v1/invoices/{theirs}:void",
            json={"organization_id": str(org_id), "reason": "not yours"},
            headers=service_token_headers(settings()),
        )
        assert resp.status_code == 404
        async with admin_session.begin():
            state = (
                await admin_session.execute(select(Invoice.state).where(Invoice.id == theirs))
            ).scalar_one()
        assert state == INVOICE_FINALIZED


def _read_ctx(org_id: uuid.UUID):
    from platform_core.auth import Scope, ScopeContext

    principal = customer_principal(org_id)
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission="core.billing.invoice.read"
    )


# --------------------------------------------------------------------- HTTP: authz & tokens


class TestHttpAuthorization:
    async def test_the_customer_read_requires_the_permission(
        self, client, org_id: uuid.UUID
    ) -> None:
        with as_principal(customer_principal(org_id, perms=[])):
            resp = await client.get("/api/v1/invoices")
        assert resp.status_code == 403

    async def test_the_credit_balance_read_requires_the_permission(
        self, client, org_id: uuid.UUID
    ) -> None:
        with as_principal(customer_principal(org_id, perms=[INVOICE_READ])):  # invoice, not credit
            resp = await client.get("/api/v1/credit-balance", params={"currency": "USD"})
        assert resp.status_code == 403

    async def test_an_org_scope_floor_rejects_a_narrower_grant(
        self, client, org_id: uuid.UUID
    ) -> None:
        with as_principal(customer_principal(org_id, perms=["core.billing.invoice.read:OWN"])):
            resp = await client.get("/api/v1/invoices")
        assert resp.status_code == 403

    async def test_the_internal_api_requires_a_service_token(
        self, client, org_id: uuid.UUID
    ) -> None:
        resp = await client.post(
            "/internal/v1/credit",
            json={
                "organization_id": str(org_id),
                "amount_minor": 1000,
                "currency": "USD",
                "reason": "no token",
            },
        )
        assert resp.status_code == 401


class TestHttpEndToEnd:
    async def test_finalize_over_internal_then_read_as_the_customer(
        self, client, org_id: uuid.UUID
    ) -> None:
        headers = service_token_headers(settings())
        resp = await client.post(
            "/internal/v1/invoices:finalize",
            headers=headers,
            json={
                "organization_id": str(org_id),
                "number": "HTTP-1",
                "currency": "USD",
                "period_start": _PERIOD_START.isoformat(),
                "period_end": _PERIOD_END.isoformat(),
                "lines": [
                    {"description": "Fee", "quantity": "1", "amount_minor": 1000},
                    {"description": "Seats", "quantity": "2", "amount_minor": 500},
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["state"] == "finalized"
        assert body["total_minor"] == 1500
        assert len(body["lines"]) == 2

        with as_principal(customer_principal(org_id, perms=[INVOICE_READ, CREDIT_READ])):
            listing = await client.get("/api/v1/invoices")
        assert listing.status_code == 200
        numbers = [inv["number"] for inv in listing.json()]
        assert "HTTP-1" in numbers

    async def test_grant_then_read_the_windowed_balance_over_http(
        self, client, org_id: uuid.UUID
    ) -> None:
        headers = service_token_headers(settings())
        grant = await client.post(
            "/internal/v1/credit",
            headers=headers,
            json={
                "organization_id": str(org_id),
                "amount_minor": 2500,
                "currency": "USD",
                "reason": "goodwill",
            },
        )
        assert grant.status_code == 201, grant.text

        with as_principal(customer_principal(org_id, perms=[CREDIT_READ])):
            balance = await client.get("/api/v1/credit-balance", params={"currency": "USD"})
        assert balance.status_code == 200
        assert balance.json()["balance_minor"] == 2500
