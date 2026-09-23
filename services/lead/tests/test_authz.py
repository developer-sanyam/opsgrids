"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a *list*,
`ctx.assert_can_touch` guards a single *object* — because filtering the list and forgetting the
detail is the named vulnerability. A rep who can list only their own leads must also be refused
another's by id, and refused converting it.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import clients, selectors
from app.main import app
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import ForbiddenProblem
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


@pytest.fixture(autouse=True)
def _stub_conversion_targets(monkeypatch: pytest.MonkeyPatch):
    """No test in this module exercises a real conversion outcome — `test_convert.py` owns
    that — so the internal calls are stubbed to succeed, and what is under test is purely
    whether the route was reached at all."""

    async def _company(settings, organization_id, **kwargs):
        return uuid.uuid4(), None

    async def _deal(settings, organization_id, **kwargs):
        return uuid.uuid4()

    monkeypatch.setattr(clients, "ensure_company_and_contact", _company)
    monkeypatch.setattr(clients, "ensure_deal", _deal)


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _names(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        rows = (await session.execute(selectors.leads_stmt(ctx))).scalars().all()
        return {lead.full_name for lead in rows}

    async def test_org_scope_sees_every_lead(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.lead.lead.read", Scope.ORG)
        assert await self._names(session, ctx) == {"Priya Sharma", "Arjun Verma"}

    async def test_own_scope_sees_only_leads_you_own(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.lead.lead.read", Scope.OWN, subject_ref=data["owner_a"])
        assert await self._names(session, ctx) == {"Priya Sharma"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(org_id, "crm.lead.lead.read", Scope.OWN, subject_ref=None)
        assert await self._names(session, ctx) == set()

    async def test_team_scope_is_refused_rather_than_widened(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.lead.lead.read", Scope.TEAM, subject_ref=data["owner_a"])
        with pytest.raises(ForbiddenProblem, match="TEAM"):
            selectors.leads_stmt(ctx)


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.lead.lead.read:OWN"], subject_ref=data["owner_a"])
        with as_principal(principal):
            refused = await client.get(f"/api/v1/leads/{data['lead_b_id']}")
            allowed = await client.get(f"/api/v1/leads/{data['lead_a_id']}")
        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["full_name"] == "Priya Sharma"

    async def test_an_own_writer_cannot_reassign_someone_elses_lead_to_themselves(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["crm.lead.lead.write:OWN"], subject_ref=data["owner_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/leads/{data['lead_b_id']}", json={"notes": "sneaky"}
            )
        assert response.status_code == 403

    async def test_an_own_convert_cannot_convert_someone_elses_lead(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["crm.lead.lead.convert:OWN"], subject_ref=data["owner_b"]
        )
        with as_principal(principal):
            response = await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert")
        assert response.status_code == 403


# --------------------------------------------------------------------- endpoint authz


def _read_endpoints(data: dict) -> list[str]:
    return [
        "/api/v1/leads",
        f"/api/v1/leads/{data['lead_a_id']}",
        "/api/v1/leads/assignment-rule",
    ]


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        for path in _read_endpoints(data):
            response = await client.get(path)
            assert response.status_code == 401, path

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to CRM, but holding no CRM permission at all."""
        principal = principal_for(org_id, [], subject_ref=data["owner_a"])
        with as_principal(principal):
            for path in _read_endpoints(data):
                response = await client.get(path)
                assert response.status_code == 403, path
                assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.lead.lead.read:ORG"],
            subject_ref=data["owner_a"],
            entitlements=[],
        )
        with as_principal(principal):
            response = await client.get("/api/v1/leads")
        assert response.status_code == 402

    async def test_a_reader_cannot_create_a_lead(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.lead.lead.read:ORG"], subject_ref=data["owner_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/leads",
                json={"full_name": "New Lead", "source": "manual"},
            )
        assert response.status_code == 403

    async def test_a_reader_cannot_convert(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.lead.lead.read:ORG"], subject_ref=data["owner_a"])
        with as_principal(principal):
            response = await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert")
        assert response.status_code == 403

    async def test_an_own_writer_cannot_configure_the_assignment_rule(
        self, client, org_id, data
    ) -> None:
        """ORG-only configuration: an OWN-scoped write grant is refused outright rather than
        silently allowed to reconfigure everyone's rotation."""
        principal = principal_for(org_id, ["crm.lead.lead.write:OWN"], subject_ref=data["owner_a"])
        with as_principal(principal):
            response = await client.put(
                "/api/v1/leads/assignment-rule",
                json={"mode": "round_robin", "owner_ids": [str(uuid.uuid4())]},
            )
        assert response.status_code == 403

    async def test_an_own_reader_sees_only_their_leads_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["crm.lead.lead.read:OWN"], subject_ref=data["owner_a"])
        with as_principal(principal):
            response = await client.get("/api/v1/leads")
        assert response.status_code == 200
        names = {item["full_name"] for item in response.json()["items"]}
        assert names == {"Priya Sharma"}

    async def test_an_own_convert_may_convert_their_own_qualified_lead(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["crm.lead.lead.convert:OWN"], subject_ref=data["owner_a"]
        )
        with as_principal(principal):
            response = await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert")
        assert response.status_code == 200
        body = response.json()
        assert body["lead_id"] == str(data["lead_a_id"])
        assert body["company_id"]
        assert body["deal_id"]

    async def test_the_wire_shape_matches_the_contract(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.lead.lead.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/leads")
        row = response.json()["items"][0]
        assert set(row) == {
            "id",
            "full_name",
            "company",
            "email",
            "phone",
            "source",
            "status",
            "owner_id",
            "notes",
            "converted_company_id",
            "converted_contact_id",
            "converted_deal_id",
            "converted_at",
            "created_at",
        }

    async def test_the_masked_email_and_phone_never_the_raw_value(
        self, client, org_id, data
    ) -> None:
        """The lead was created with a real email and phone (`conftest.py`'s `data` fixture);
        the wire form must be the mask, never the plaintext that went in."""
        principal = principal_for(org_id, ["crm.lead.lead.read:ORG"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/leads/{data['lead_a_id']}")
        body = response.json()
        assert body["email"] != "priya@stellar.example"
        assert "•" in body["email"]  # the masking glyph
        assert body["phone"] != "+919876543210"
        assert "•" in body["phone"]
