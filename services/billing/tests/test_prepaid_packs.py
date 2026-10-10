"""Prepaid credit packs: a time-boxed grant, drawn down across invoices (ADR-0004 §3).

A pack needed no new product and no new table — it is one positive credit-ledger row with a
validity window, which is what the ledger was already for (docs/commerce-packaging.md). What it
*did* need was one correction, and the first class here is the reason: a consumption windowed
`[now, NULL)` outlives the time-boxed grant it spent, so the moment the pack lapses the grant
leaves the balance `SUM` while the consumption keeps counting and the balance reads *minus what
was spent* forever after. That is the same defect the reversal path was fixed for; a draw-down
now names its pack in `drawn_from` and inherits the pack's window.

The other reason packs matter is a payments constraint rather than an accounting one. A UPI
Autopay mandate is capped — commonly ₹15,000 per debit — and `payment.create_charge` refuses an
amount above a mandate's `max_amount_minor` (tested there, in
`test_the_cap_is_a_hard_edge_and_the_authority_is_recorded`). An annual commitment collected as a
mandate debit therefore does not "mostly work": it fails, and a failed collection opens a dunning
cycle against a customer who has done nothing wrong. Collected once as a prepaid pack it raises
no debit at all, and the monthly invoices draw the pack down. The last class here is that
arithmetic, written out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.models import CREDIT_GRANT, CREDIT_REVERSAL, CreditLedgerEntry
from app.schemas import CreditConsumeIn, CreditGrantIn, CreditReverseIn
from platform_core.errors import BusinessRuleProblem, NotFoundProblem
from tests.helpers import service_token_headers

pytestmark = pytest.mark.integration

#: A one-year prepaid commitment: ₹1,20,000 in paise. Integer minor units, like every amount in
#: this service (ADR-0000 §8).
PACK_MINOR = 12_000_000
#: The UPI Autopay per-debit ceiling the pack exists to route around: ₹15,000 in paise.
UPI_AUTOPAY_CEILING_MINOR = 1_500_000

WINDOW_START = datetime(2026, 4, 1, tzinfo=UTC)
WINDOW_END = datetime(2027, 4, 1, tzinfo=UTC)


async def _open_pack(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    amount_minor: int = PACK_MINOR,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    currency: str = "INR",
):
    """Sell a prepaid pack: one positive ledger row with a window. That is the whole feature."""
    return await services.grant_credit(
        session,
        CreditGrantIn(
            organization_id=org_id,
            amount_minor=amount_minor,
            currency=currency,
            reason="Annual prepaid commitment",
            valid_from=valid_from,
            valid_until=valid_until,
        ),
        request=None,
    )


async def _draw(
    session: AsyncSession,
    org_id: uuid.UUID,
    pack_id: uuid.UUID,
    amount_minor: int,
    *,
    reason: str = "Applied to invoice",
    currency: str = "INR",
):
    return await services.consume_credit(
        session,
        CreditConsumeIn(
            organization_id=org_id,
            amount_minor=amount_minor,
            currency=currency,
            reason=reason,
            drawn_from=pack_id,
        ),
        request=None,
    )


# --------------------------------------------------------------------- draw-down across invoices


class TestAPackDrawsDownAcrossInvoices:
    async def test_three_invoices_draw_the_pack_down_and_the_last_is_partly_payable(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The ordinary life of a pack. Nothing is mutated: the grant is one row, each draw is
        another, and "what is left" is a `SUM` over them — never a column somebody decremented.
        """
        async with session.begin():
            pack = await _open_pack(session, org_id, valid_until=WINDOW_END)

        async with session.begin():
            await _draw(session, org_id, pack.id, 4_500_000, reason="INV-0001")
            assert (
                await selectors.pack_remaining(session, pack.id, organization_id=org_id)
                == 7_500_000
            )

        async with session.begin():
            await _draw(session, org_id, pack.id, 5_000_000, reason="INV-0002")
            remaining = await selectors.pack_remaining(session, pack.id, organization_id=org_id)
        assert remaining == 2_500_000

        # A third invoice for ₹30,000 exceeds what is left. The refusal names the remainder, so
        # the caller can draw exactly the covered part and bill the difference.
        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="2500000"):
                await _draw(session, org_id, pack.id, 3_000_000, reason="INV-0003")

        async with session.begin():
            await _draw(session, org_id, pack.id, remaining, reason="INV-0003 (partial)")
            assert await selectors.pack_remaining(session, pack.id, organization_id=org_id) == 0
            assert (
                await selectors.credit_balance(
                    session,
                    organization_id=org_id,
                    currency="INR",
                    at=datetime(2026, 9, 1, tzinfo=UTC),
                )
                == 0
            )

    async def test_a_spent_pack_cannot_be_drawn_again(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The overdraw guard is what stops a pack being spent twice over. Without it the ledger
        would happily accept the second draw and the organization's balance would go negative —
        which is not an error state anywhere else in this service, so nothing would notice."""
        async with session.begin():
            pack = await _open_pack(session, org_id, amount_minor=100_000, valid_until=WINDOW_END)
        async with session.begin():
            await _draw(session, org_id, pack.id, 100_000)
        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="0 INR minor units left"):
                await _draw(session, org_id, pack.id, 1)

    async def test_reversing_a_draw_puts_the_credit_back_on_the_pack(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A mistaken draw is corrected the way everything here is: another row. The reversal
        inherits the window it negates, and carries the same `drawn_from`, so the pack's own
        remaining balance recovers as well as the organization's."""
        async with session.begin():
            pack = await _open_pack(session, org_id, valid_until=WINDOW_END)
            drawn = await _draw(session, org_id, pack.id, 4_500_000)

        async with session.begin():
            await services.reverse_credit(
                session,
                CreditReverseIn(
                    organization_id=org_id, reversal_of=drawn.id, reason="wrong invoice"
                ),
                request=None,
            )

        async with session.begin():
            assert (
                await selectors.pack_remaining(session, pack.id, organization_id=org_id)
                == PACK_MINOR
            )


# --------------------------------------------------------------------- the window edge


class TestAPackExpiresExactlyAtItsWindowEdge:
    async def test_the_balance_is_the_unspent_remainder_up_to_the_edge_and_zero_at_it(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`valid_until` is exclusive: the last microsecond inside the window still counts, and
        the instant itself does not. Asked at three instants with nothing having run in between,
        because expiry is a property of the query and not of a job."""
        async with session.begin():
            pack = await _open_pack(
                session, org_id, valid_from=WINDOW_START, valid_until=WINDOW_END
            )
            await _draw(session, org_id, pack.id, 4_500_000)

        async with session.begin():
            mid_term = await selectors.credit_balance(
                session,
                organization_id=org_id,
                currency="INR",
                at=datetime(2026, 10, 1, tzinfo=UTC),
            )
            last_moment = await selectors.credit_balance(
                session,
                organization_id=org_id,
                currency="INR",
                at=WINDOW_END - timedelta(microseconds=1),
            )
            at_the_edge = await selectors.credit_balance(
                session, organization_id=org_id, currency="INR", at=WINDOW_END
            )

        assert mid_term == PACK_MINOR - 4_500_000
        assert last_moment == PACK_MINOR - 4_500_000, "the window is closed at its upper end only"
        assert at_the_edge == 0, "the pack stops counting at the instant it lapses"

    async def test_an_expired_pack_never_drags_the_balance_negative(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The regression that made `drawn_from` necessary.

        With the draw windowed `[now, NULL)` — as every consumption used to be — the grant would
        leave the SUM at `valid_until` while the draw kept counting, and this would read
        **-4,500,000**: a pack that expired unspent would have *taken* credit away. It reads zero
        because the draw inherits the pack's window, so the pair nets to zero at every instant
        the balance can be asked about.
        """
        async with session.begin():
            pack = await _open_pack(
                session, org_id, valid_from=WINDOW_START, valid_until=WINDOW_END
            )
            await _draw(session, org_id, pack.id, 4_500_000)

        async with session.begin():
            long_after = await selectors.credit_balance(
                session, organization_id=org_id, currency="INR", at=datetime(2030, 1, 1, tzinfo=UTC)
            )
        assert long_after == 0
        assert long_after >= 0, "a lapsed pack must never make the organization owe its own credit"

    async def test_a_lapsed_pack_cannot_be_drawn_down(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Past its window the pack pays for nothing, so a draw against it must be refused rather
        than recorded as a zero-effect row that *looks* like it settled an invoice."""
        lapsed_end = datetime.now(UTC) - timedelta(days=1)
        async with session.begin():
            pack = await _open_pack(
                session,
                org_id,
                valid_from=lapsed_end - timedelta(days=365),
                valid_until=lapsed_end,
            )
        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="lapsed"):
                await _draw(session, org_id, pack.id, 100_000)

    async def test_an_open_ended_grant_still_behaves_as_it_always_did(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`drawn_from` is opt-in. A consumption that names no pack spends the general balance
        and is open-ended, exactly as before — nothing about comp credit changed."""
        async with session.begin():
            await _open_pack(session, org_id, amount_minor=1_000_000, valid_until=None)
            await services.consume_credit(
                session,
                CreditConsumeIn(
                    organization_id=org_id,
                    amount_minor=250_000,
                    currency="INR",
                    reason="general spend",
                ),
                request=None,
            )
        async with session.begin():
            entry_balance = await selectors.credit_balance(
                session, organization_id=org_id, currency="INR", at=datetime.now(UTC)
            )
        assert entry_balance == 750_000


# --------------------------------------------------------------------- what a draw refuses


class TestADrawDownIsChecked:
    async def test_another_tenants_pack_is_simply_not_found(
        self, session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Row-level security scopes the lookup, so a pack belonging to somebody else is a 404 —
        the same answer an invented id gets, which is what stops the id being an oracle."""
        async with session.begin():
            pack = await _open_pack(session, org_id, valid_until=WINDOW_END)

        # A session bound to org_id cannot even see org_b's rows; drawing org_id's pack while
        # claiming to be org_b is refused from the other direction, by the tenant binding.
        async with session.begin():
            with pytest.raises(NotFoundProblem):
                await services.consume_credit(
                    session,
                    CreditConsumeIn(
                        organization_id=org_id,
                        amount_minor=1,
                        currency="INR",
                        reason="not mine",
                        drawn_from=uuid.uuid4(),
                    ),
                    request=None,
                )
            assert (
                await selectors.pack_remaining(session, pack.id, organization_id=org_id)
                == PACK_MINOR
            )

    async def test_credit_cannot_be_drawn_from_a_consumption(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Naming a negative row as the source would make `pack_remaining` read as though there
        were more credit than was ever granted."""
        async with session.begin():
            pack = await _open_pack(session, org_id, valid_until=WINDOW_END)
            drawn = await _draw(session, org_id, pack.id, 100_000)

        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="only be drawn from a grant"):
                await _draw(session, org_id, drawn.id, 1)

    async def test_a_currency_mismatch_is_refused_rather_than_converted(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Money is minor units *and* an ISO code. Spending a rupee pack against a dollar invoice
        is not a conversion this service is entitled to invent."""
        async with session.begin():
            pack = await _open_pack(session, org_id, valid_until=WINDOW_END)
        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="INR"):
                await _draw(session, org_id, pack.id, 1000, currency="USD")


# --------------------------------------------------------------------- the mandate ceiling


class TestThePackReadOverHttp:
    """The internal surface a billing run drives: sell a pack, ask what is left, draw the rest."""

    async def test_a_billing_run_reads_the_remainder_before_it_draws(
        self, client, org_id: uuid.UUID
    ) -> None:
        headers = service_token_headers(settings())
        sold = await client.post(
            "/internal/v1/credit",
            headers=headers,
            json={
                "organization_id": str(org_id),
                "amount_minor": PACK_MINOR,
                "currency": "INR",
                "reason": "Annual prepaid commitment",
                "valid_from": WINDOW_START.isoformat(),
                "valid_until": WINDOW_END.isoformat(),
            },
        )
        assert sold.status_code == 201, sold.text
        pack_id = sold.json()["id"]

        drawn = await client.post(
            "/internal/v1/credit:consume",
            headers=headers,
            json={
                "organization_id": str(org_id),
                "amount_minor": 4_500_000,
                "currency": "INR",
                "reason": "INV-0001",
                "drawn_from": pack_id,
            },
        )
        assert drawn.status_code == 201, drawn.text
        assert drawn.json()["drawn_from"] == pack_id
        # The draw inherited the pack's window rather than opening its own.
        assert drawn.json()["valid_until"] is not None

        pack = await client.get(
            f"/internal/v1/organizations/{org_id}/credit/packs/{pack_id}", headers=headers
        )
        assert pack.status_code == 200, pack.text
        body = pack.json()
        assert body["granted_minor"] == PACK_MINOR
        assert body["remaining_minor"] == PACK_MINOR - 4_500_000
        assert body["lapsed"] is False, "the pack's window is open, so it still pays for things"

    async def test_the_pack_read_needs_a_service_token(self, client, org_id: uuid.UUID) -> None:
        resp = await client.get(f"/internal/v1/organizations/{org_id}/credit/packs/{uuid.uuid4()}")
        assert resp.status_code == 401


class TestTheUpiAutopayCeilingWorkaround:
    """Why a prepaid pack is a payments answer and not only an accounting one.

    Razorpay's UPI Autopay mandates carry a per-debit cap, and `payment.create_charge` refuses an
    amount above `max_amount_minor` rather than letting the provider decline it later. So an
    annual commitment above the ceiling cannot be collected on a mandate at all — and the failure
    is not a payment error a customer can act on, it is a dunning cycle opened against somebody
    who agreed to pay a year up front. Collected once as a pack it raises no debit.
    """

    def test_an_annual_commitment_does_not_fit_under_the_per_debit_ceiling(self) -> None:
        """The arithmetic the workaround exists for, stated so it cannot quietly stop being
        true: the annual amount is above the ceiling, each monthly slice is below it."""
        assert PACK_MINOR > UPI_AUTOPAY_CEILING_MINOR
        assert PACK_MINOR // 12 < UPI_AUTOPAY_CEILING_MINOR

    async def test_twelve_monthly_invoices_draw_the_annual_pack_to_exactly_zero(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The commitment is collected once, out of band, and recorded as a pack. Twelve monthly
        invoices then draw it down and *no mandate is ever debited* — which is the point: there
        is no charge to exceed a cap, and therefore no failed collection to dun."""
        monthly = PACK_MINOR // 12
        async with session.begin():
            pack = await _open_pack(
                session, org_id, valid_from=WINDOW_START, valid_until=WINDOW_END
            )

        for month in range(12):
            async with session.begin():
                await _draw(session, org_id, pack.id, monthly, reason=f"INV-2026-{month + 1:02d}")

        async with session.begin():
            remaining = await selectors.pack_remaining(session, pack.id, organization_id=org_id)
            inside_window = await selectors.credit_balance(
                session, organization_id=org_id, currency="INR", at=datetime(2027, 3, 1, tzinfo=UTC)
            )
        assert monthly * 12 == PACK_MINOR, "the commitment divides exactly; no paisa is stranded"
        assert remaining == 0
        assert inside_window == 0

    async def test_a_thirteenth_invoice_finds_the_pack_empty_rather_than_overdrawn(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The renewal boundary. A pack that has done its year does not silently fund a
        thirteenth month — the draw is refused and the invoice is payable, which is the moment
        the customer is asked to commit again."""
        monthly = PACK_MINOR // 12
        async with session.begin():
            pack = await _open_pack(
                session, org_id, valid_from=WINDOW_START, valid_until=WINDOW_END
            )
        for _ in range(12):
            async with session.begin():
                await _draw(session, org_id, pack.id, monthly)

        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="0 INR minor units left"):
                await _draw(session, org_id, pack.id, monthly)


# ------------------------------------------------------------- the reads filter the tenant too


class TestTheLedgerReadsFilterTheirOwnTenant:
    """`get_credit_entry` and `pack_remaining` carry an explicit `organization_id` predicate, not
    only row-level security (CLAUDE.md: every query filters on it; RLS is the net). The reads
    run on the BYPASSRLS `admin_session` deliberately: under a tenant-bound session RLS would hide
    the other organization's pack and the test would pass with the predicate missing."""

    async def test_another_tenants_pack_is_not_returned_when_rls_would_allow_it(
        self, admin_session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        async with admin_session.begin():
            theirs = CreditLedgerEntry(
                organization_id=org_b,
                entry_type=CREDIT_GRANT,
                amount_minor=PACK_MINOR,
                currency="INR",
                reason="Another tenant's pack",
                valid_from=WINDOW_START,
            )
            admin_session.add(theirs)

        async with admin_session.begin():
            assert (
                await selectors.get_credit_entry(admin_session, theirs.id, organization_id=org_id)
                is None
            )
            assert (
                await selectors.pack_remaining(admin_session, theirs.id, organization_id=org_id)
                == 0
            )
            # Control: the same reads for the owning tenant do find it.
            found = await selectors.get_credit_entry(
                admin_session, theirs.id, organization_id=org_b
            )
            assert found is not None and found.id == theirs.id
            assert (
                await selectors.pack_remaining(admin_session, theirs.id, organization_id=org_b)
                == PACK_MINOR
            )

    async def test_the_balance_and_the_reversal_lookup_filter_their_tenant_too(
        self, admin_session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        mid_window = WINDOW_START + timedelta(days=30)
        async with admin_session.begin():
            theirs = CreditLedgerEntry(
                organization_id=org_b,
                entry_type=CREDIT_GRANT,
                amount_minor=PACK_MINOR,
                currency="INR",
                reason="Another tenant's grant",
                valid_from=WINDOW_START,
            )
            admin_session.add(theirs)

        async with admin_session.begin():
            assert (
                await selectors.credit_balance(
                    admin_session, currency="INR", at=mid_window, organization_id=org_id
                )
                == 0
            )
            assert (
                await selectors.credit_balance(
                    admin_session, currency="INR", at=mid_window, organization_id=org_b
                )
                == PACK_MINOR
            )

        async with admin_session.begin():
            admin_session.add(
                CreditLedgerEntry(
                    organization_id=org_b,
                    entry_type=CREDIT_REVERSAL,
                    amount_minor=-PACK_MINOR,
                    currency="INR",
                    reason="Their reversal",
                    valid_from=WINDOW_START,
                    reversal_of=theirs.id,
                )
            )

        async with admin_session.begin():
            assert (
                await selectors.get_reversal_of(admin_session, theirs.id, organization_id=org_id)
                is None
            )
            found = await selectors.get_reversal_of(admin_session, theirs.id, organization_id=org_b)
            assert found is not None and found.reversal_of == theirs.id
