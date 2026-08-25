"""The demo seed produces the expected rows, and is idempotent — the book half.

Drives `tools/demo/seed_crm.py`'s `seed_customer` directly against a tenant-bound session
(the seed module is importable and side-effect-free at load), so this proves the same
dataset the live screens render lands correctly: the fixture counts, the deterministic
ids the pipeline's cross-schema references rely on, and the contact→company links.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import selectors
from platform_core.auth import Scope
from tests.helpers import ctx_for

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "demo"))
import seed_crm

pytestmark = pytest.mark.integration


class TestDemoSeedBook:
    async def test_seed_inserts_the_full_fixture_dataset(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            counts = await seed_crm.seed_customer(session, ctx)
        assert counts == {"owners": 6, "companies": 15, "contacts": 18, "skipped": 0}

    async def test_seed_is_idempotent(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_customer(session, ctx)
        async with session.begin():
            second = await seed_crm.seed_customer(session, ctx)
        assert second["owners"] == 0
        assert second["companies"] == 0
        assert second["contacts"] == 0
        assert second["skipped"] == 6 + 15 + 18

    async def test_rows_land_on_their_deterministic_ids(self, session, org_id) -> None:
        """The pipeline references these rows by `demo_id(org, key)` — the ids must be
        exactly where the deal seed will point."""
        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_customer(session, ctx)
        company = await selectors.get_company(session, org_id, seed_crm.demo_id(org_id, "comp-1"))
        assert company is not None
        assert company.name == "Northwind Logistics"
        assert company.owner_id == seed_crm.demo_id(org_id, "own-1")
        assert company.annual_revenue_minor == 8_400_000_000 * 100
        assert company.currency == "INR"

    async def test_contacts_cross_link_to_their_company(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_customer(session, ctx)
        contact = await selectors.get_contact(session, org_id, seed_crm.demo_id(org_id, "con-17"))
        assert contact is not None
        assert contact.full_name == "Vivek Nambiar"
        assert contact.company_id == seed_crm.demo_id(org_id, "comp-6")
        assert contact.owner_id == seed_crm.demo_id(org_id, "own-1")
        # The masked display form is stored verbatim; no raw number exists anywhere.
        assert "•" in contact.phone  # the fixture's masking glyph (bullet)

    async def test_the_rep_directory_matches_the_fixtures(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_customer(session, ctx)
        reps = await selectors.owners(session, org_id)
        assert len(reps) == 6
        assert {r.full_name for r in reps} == {
            "Sofia Marchetti",
            "Vikram Singh",
            "Grace Adeyemi",
            "Kabir Malhotra",
            "Emily Carter",
            "Rahul Deshpande",
        }
