"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth
more than proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a
*list*, `ctx.assert_can_touch` guards a single *object* — because filtering the list and
forgetting the detail is the named vulnerability. A rep who can list only their own
accounts must also be refused another's by id.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import ConflictProblem, NotFoundProblem
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _company_names(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        rows = (await session.execute(selectors.companies_stmt(ctx))).scalars().all()
        return {c.name for c in rows}

    async def test_org_scope_sees_the_whole_book(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.book.company.read", Scope.ORG)
        assert await self._company_names(session, ctx) == {
            "Northwind Logistics",
            "Meridian Steelworks",
        }

    async def test_own_scope_sees_only_accounts_you_own(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.book.company.read", Scope.OWN, subject_ref=data["subject_a"])
        assert await self._company_names(session, ctx) == {"Northwind Logistics"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(org_id, "crm.book.company.read", Scope.OWN, subject_ref=None)
        assert await self._company_names(session, ctx) == set()

    async def test_a_search_cannot_widen_scope(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.book.company.read", Scope.OWN, subject_ref=data["subject_a"])
        stmt = selectors.companies_stmt(ctx, q="steel")  # matches only subject_b's company
        assert (await session.execute(stmt)).all() == []

    async def test_own_scope_narrows_contacts_too(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.book.contact.read", Scope.OWN, subject_ref=data["subject_b"])
        rows = (await session.execute(selectors.contacts_stmt(ctx))).scalars().all()
        assert {c.full_name for c in rows} == {"Suresh Iyer"}


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_company_detail_route(
        self, client, org_id, data
    ) -> None:
        """A caller with OWN scope is refused another rep's company by id, but not their own."""
        principal = principal_for(
            org_id, ["crm.book.company.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            refused = await client.get(f"/api/v1/customers/{data['company_b_id']}")
            allowed = await client.get(f"/api/v1/customers/{data['company_a_id']}")
        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["name"] == "Northwind Logistics"

    async def test_scope_does_not_leak_through_the_contact_detail_route(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["crm.book.contact.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            refused = await client.get(f"/api/v1/contacts/{data['contact_b_id']}")
            allowed = await client.get(f"/api/v1/contacts/{data['contact_a_id']}")
        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["full_name"] == "Rajesh Khanna"


# --------------------------------------------------------------------- endpoint authz


def _read_endpoints(data: dict) -> list[str]:
    return [
        "/api/v1/customers",
        "/api/v1/customers/owners",
        f"/api/v1/customers/{data['company_a_id']}",
        "/api/v1/contacts",
        f"/api/v1/contacts/{data['contact_a_id']}",
    ]


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        for path in _read_endpoints(data):
            response = await client.get(path)
            assert response.status_code == 401, path

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to CRM, but holding no CRM permission at all."""
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            for path in _read_endpoints(data):
                response = await client.get(path)
                assert response.status_code == 403, path
                assert response.headers["content-type"].startswith("application/problem+json")

    async def test_the_contact_permission_does_not_open_the_company_list(
        self, client, org_id, data
    ) -> None:
        """Permissions are per resource: contact.read alone reads no companies (and the
        other way around) — a coarse 'has any crm permission' check would be a leak."""
        contact_only = principal_for(
            org_id, ["crm.book.contact.read:ORG"], subject_ref=data["subject_a"]
        )
        company_only = principal_for(
            org_id, ["crm.book.company.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(contact_only):
            companies = await client.get("/api/v1/customers")
        with as_principal(company_only):
            contacts = await client.get("/api/v1/contacts")
        assert companies.status_code == 403
        assert contacts.status_code == 403

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.book.company.read:ORG", "crm.book.contact.read:ORG"],
            subject_ref=data["subject_a"],
            entitlements=[],
        )
        with as_principal(principal):
            companies = await client.get("/api/v1/customers")
            contacts = await client.get("/api/v1/contacts")
        assert companies.status_code == 402
        assert contacts.status_code == 402

    async def test_an_own_reader_sees_only_their_accounts_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["crm.book.company.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/customers")
        assert response.status_code == 200
        names = {item["name"] for item in response.json()["items"]}
        assert names == {"Northwind Logistics"}

    async def test_the_wire_shape_matches_the_spa_contract(self, client, org_id, data) -> None:
        """The list row carries exactly the SPA's `Company` fields — the flag-flip check."""
        principal = principal_for(
            org_id, ["crm.book.company.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/customers")
        row = response.json()["items"][0]
        assert set(row) == {
            "id",
            "name",
            "domain",
            "industry",
            "size_band",
            "city",
            "country",
            "status",
            "owner_id",
            "annual_revenue_minor",
            "currency",
            "created_at",
        }
        assert isinstance(row["annual_revenue_minor"], int)

    async def test_owners_resolve_for_any_reader_in_scope(self, client, org_id, data) -> None:
        """The rep directory is reference data: an OWN-scoped caller still resolves every
        owner name, or their own rows would render blank."""
        principal = principal_for(
            org_id, ["crm.book.company.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/customers/owners")
        assert response.status_code == 200
        names = {o["full_name"] for o in response.json()}
        assert names == {"Sofia Marchetti", "Kabir Malhotra"}


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_a_duplicate_company_name_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_company(
                    session,
                    ctx,
                    name="Northwind Logistics",
                    owner_id=data["subject_a"],
                )

    async def test_a_contact_against_an_unknown_company_is_refused(
        self, session, org_id, data
    ) -> None:
        ctx = ctx_for(org_id, "crm.book.contact.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="company"):
            async with session.begin():
                await services.create_contact(
                    session,
                    ctx,
                    full_name="Nobody",
                    email="nobody@example.com",
                    company_id=uuid.uuid4(),
                    owner_id=data["subject_a"],
                )

    async def test_creating_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data
    ) -> None:
        """The change, its audit row and its event commit together — nothing publishes inline."""
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            company = await services.create_company(
                session, ctx, name="Audited & Co", owner_id=data["subject_a"]
            )
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "company.created", AuditLog.entity_id == company.id)
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == company.id)
                )
            )
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1
        assert events[0].event_type == "crm.book.company.created"
        assert events[0].published_at is None
