"""GST tax-invoice groundwork (ADR-0004 Q1): place of supply, the head split, the supplier's
tax-invoice series and the optional supplier settings. None of it is wired into finalize yet.

Every GSTIN here is fake — a made-up PAN with its check digit computed in the test.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import domain, services
from app.config import Settings, settings
from app.models import SCHEMA
from platform_core import gst
from platform_core.errors import BusinessRuleProblem


def _fake_gstin(state: str) -> str:
    base = f"{state}ABCDE1234F1Z"
    return base + gst.gstin_check_digit(base)


SUPPLIER = "29"  # a fixture state, not the operator's


class TestPlaceOfSupply:
    def test_the_buyer_gstin_state_wins(self) -> None:
        pos = domain.place_of_supply(
            supplier_state_code=SUPPLIER, buyer_gstin=_fake_gstin("27"), profile_state_code="33"
        )
        assert pos == "27"

    def test_without_a_gstin_the_profile_state_is_used(self) -> None:
        pos = domain.place_of_supply(
            supplier_state_code=SUPPLIER, buyer_gstin=None, profile_state_code="33"
        )
        assert pos == "33"

    def test_b2c_without_an_address_falls_back_to_the_supplier_state(self) -> None:
        pos = domain.place_of_supply(
            supplier_state_code=SUPPLIER, buyer_gstin=None, profile_state_code=None
        )
        assert pos == SUPPLIER

    def test_a_bad_gstin_or_state_raises_rather_than_falling_through(self) -> None:
        good = _fake_gstin("27")
        bad = good[:14] + ("0" if good[14] != "0" else "1")
        with pytest.raises(gst.GstinError):
            domain.place_of_supply(
                supplier_state_code=SUPPLIER, buyer_gstin=bad, profile_state_code=None
            )
        with pytest.raises(ValueError, match="state code"):
            domain.place_of_supply(
                supplier_state_code=SUPPLIER, buyer_gstin=None, profile_state_code="00"
            )


class TestTaxHeads:
    def test_intra_state_splits_cgst_and_sgst(self) -> None:
        heads = domain.tax_heads(1800, supplier_state_code=SUPPLIER, place_of_supply=SUPPLIER)
        assert heads == gst.TaxSplit(cgst_minor=900, sgst_minor=900)

    def test_odd_paisa_goes_to_sgst_and_the_heads_foot(self) -> None:
        heads = domain.tax_heads(1801, supplier_state_code=SUPPLIER, place_of_supply=SUPPLIER)
        assert heads == gst.TaxSplit(cgst_minor=900, sgst_minor=901)
        assert heads.total_minor == 1801

    def test_inter_state_is_all_igst(self) -> None:
        heads = domain.tax_heads(1801, supplier_state_code=SUPPLIER, place_of_supply="27")
        assert heads == gst.TaxSplit(igst_minor=1801)

    @pytest.mark.parametrize("supply_type", ["sez", "export"])
    def test_sez_and_export_are_igst_even_in_the_supplier_state(self, supply_type: str) -> None:
        heads = domain.tax_heads(
            1801, supplier_state_code=SUPPLIER, place_of_supply=SUPPLIER, supply_type=supply_type
        )
        assert heads == gst.TaxSplit(igst_minor=1801)


class TestTaxInvoiceNumber:
    def test_the_adr_shape(self) -> None:
        assert domain.tax_invoice_number("invoice", "2026-27", 1) == "OG/2627/000001"

    def test_an_undecided_series_or_a_bad_serial_is_refused(self) -> None:
        with pytest.raises(ValueError, match="credit_note"):
            domain.tax_invoice_number("credit_note", "2026-27", 1)
        with pytest.raises(ValueError, match="starts at 1"):
            domain.tax_invoice_number("invoice", "2026-27", 0)


class TestSupplierSettings:
    def test_all_empty_by_default(self) -> None:
        cfg = settings()
        assert cfg.billing_supplier_gstin is None
        assert cfg.billing_supplier_legal_name is None
        assert cfg.billing_supplier_state_code is None
        assert cfg.billing_default_sac is None

    def test_a_valid_gstin_is_accepted_and_normalised(self) -> None:
        gstin = _fake_gstin(SUPPLIER)
        cfg = Settings(billing_supplier_gstin=gstin.lower(), billing_supplier_state_code=SUPPLIER)
        assert cfg.billing_supplier_gstin == gstin

    def test_a_wrong_check_digit_refuses_to_boot(self) -> None:
        good = _fake_gstin(SUPPLIER)
        bad = good[:14] + ("0" if good[14] != "0" else "1")
        with pytest.raises(ValidationError, match="checksum"):
            Settings(billing_supplier_gstin=bad)

    def test_the_state_code_must_exist_and_match_the_gstin(self) -> None:
        with pytest.raises(ValidationError, match="not a GST state code"):
            Settings(billing_supplier_state_code="00")
        with pytest.raises(ValidationError, match="differs"):
            Settings(billing_supplier_gstin=_fake_gstin(SUPPLIER), billing_supplier_state_code="27")

    def test_the_sac_shape_is_checked_only_when_set(self) -> None:
        with pytest.raises(ValidationError, match="SAC"):
            Settings(billing_default_sac="12345")
        assert Settings(billing_default_sac="990000").billing_default_sac == "990000"


# ---------------------------------------------------------------------- the series, in Postgres


async def _allocate_committed(maker: async_sessionmaker[AsyncSession], on: date) -> str:
    async with maker() as s, s.begin():
        return await services.allocate_tax_invoice_number(s, series="invoice", on=on)


class TestTaxInvoiceSeries:
    async def test_concurrent_allocations_never_collide_and_are_consecutive(
        self, session: AsyncSession
    ) -> None:
        # Each allocation on its own app-role connection, all racing for the one counter row.
        engine = create_async_engine(
            settings().app_dsn,
            pool_size=10,
            connect_args={"server_settings": {"search_path": SCHEMA}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            numbers = await asyncio.gather(
                *(_allocate_committed(maker, date(2026, 10, 12)) for _ in range(10))
            )
        finally:
            await engine.dispose()
        assert sorted(numbers) == [f"OG/2627/{n:06d}" for n in range(1, 11)]

    async def test_a_rolled_back_allocation_consumes_no_number(self, session: AsyncSession) -> None:
        async with session.begin():
            first = await services.allocate_tax_invoice_number(
                session, series="invoice", on=date(2026, 4, 1)
            )
        tx = await session.begin()
        await services.allocate_tax_invoice_number(session, series="invoice", on=date(2026, 4, 1))
        await tx.rollback()
        async with session.begin():
            second = await services.allocate_tax_invoice_number(
                session, series="invoice", on=date(2027, 3, 31)
            )
        assert (first, second) == ("OG/2627/000001", "OG/2627/000002")

    async def test_a_new_financial_year_starts_again_at_one(self, session: AsyncSession) -> None:
        async with session.begin():
            await services.allocate_tax_invoice_number(
                session, series="invoice", on=date(2027, 3, 31)
            )
            nxt = await services.allocate_tax_invoice_number(
                session, series="invoice", on=date(2027, 4, 1)
            )
        assert nxt == "OG/2728/000001"

    async def test_an_undecided_series_is_refused_before_touching_the_counter(
        self, session: AsyncSession
    ) -> None:
        async with session.begin():
            with pytest.raises(BusinessRuleProblem):
                await services.allocate_tax_invoice_number(
                    session, series="credit_note", on=date(2026, 4, 1)
                )
            count = (
                await session.execute(text("SELECT count(*) FROM tax_invoice_series"))
            ).scalar()
        assert count == 0

    async def test_the_series_is_shared_across_tenants(
        self, session: AsyncSession, org_b: object
    ) -> None:
        """The supplier's series, not a tenant's: a second organization's binding continues the
        same counter instead of starting its own `000001`."""
        async with session.begin():
            await services.allocate_tax_invoice_number(
                session, series="invoice", on=date(2026, 5, 1)
            )
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.organization_id', :o, true)"), {"o": str(org_b)}
            )
            number = await services.allocate_tax_invoice_number(
                session, series="invoice", on=date(2026, 5, 1)
            )
        assert number == "OG/2627/000002"

    async def test_rls_exemption_is_explicit_and_the_app_role_cannot_delete(
        self, admin_session: AsyncSession
    ) -> None:
        """ADR-0004 Q1 decision 4: no `organization_id`, no row-level security, and the app role
        may SELECT/INSERT/UPDATE but not DELETE or TRUNCATE."""
        role = settings().db_app_role
        table = f"{SCHEMA}.tax_invoice_series"
        async with admin_session.begin():
            columns = set(
                (
                    await admin_session.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :s AND table_name = 'tax_invoice_series'"
                        ),
                        {"s": SCHEMA},
                    )
                ).scalars()
            )
            rls = (
                await admin_session.execute(
                    text("SELECT relrowsecurity FROM pg_class WHERE oid = CAST(:t AS regclass)"),
                    {"t": table},
                )
            ).scalar_one()
            privileges = {
                p: (
                    await admin_session.execute(
                        text("SELECT has_table_privilege(:r, :t, :p)"),
                        {"r": role, "t": table, "p": p},
                    )
                ).scalar_one()
                for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            }
        assert "organization_id" not in columns
        assert rls is False
        assert privileges == {
            "SELECT": True,
            "INSERT": True,
            "UPDATE": True,
            "DELETE": False,
            "TRUNCATE": False,
        }
