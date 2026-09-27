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
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clients, selectors
from app.main import app
from app.models import AuditLog
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import ForbiddenProblem
from tests.helpers import LOGIN_OTP, STEPPED_UP, ctx_for, principal_for

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


# --------------------------------------------------------------------- revealing a sealed detail
#
# `POST /leads/{id}:reveal` is the one route that returns a real phone number or email, so the
# tests here are the negative half: every way of being refused, and the proof that a refusal
# neither carries the value nor leaves a "this was revealed" audit row behind. The values it
# would have revealed are the `data` fixture's lead_a — see `test_pii.py::TestReveal` for what a
# permitted reveal returns and records.

REVEAL = "crm.lead.lead.read_sensitive"
RAW_PHONE = "+919876543210"
RAW_EMAIL = "priya@stellar.example"


def _reveal_path(lead_id: uuid.UUID) -> str:
    return f"/api/v1/leads/{lead_id}:reveal"


async def _sensitive_read_count(admin_session: AsyncSession, org_id: uuid.UUID) -> int:
    """Committed `entity.sensitive_read` rows — read on a separate BYPASSRLS connection so a
    row that was only staged in the request's transaction, never committed, does not count."""
    async with admin_session.begin():
        return (
            await admin_session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.organization_id == org_id,
                    AuditLog.action == "entity.sensitive_read",
                )
            )
        ).scalar_one()


def _assert_no_plaintext(response) -> None:  # type: ignore[no-untyped-def]
    assert RAW_PHONE not in response.text
    assert RAW_PHONE.removeprefix("+") not in response.text
    assert RAW_EMAIL not in response.text


class TestRevealAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 401

    async def test_holding_no_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [], subject_ref=data["owner_a"], amr=STEPPED_UP)
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        assert f"Missing permission: {REVEAL}" in response.json()["detail"]
        _assert_no_plaintext(response)

    async def test_the_ordinary_lead_permissions_do_not_reveal(self, client, org_id, data) -> None:
        """The reason the permission exists: read, write and convert at the widest scope, with
        a second factor, still do not open a prospect's phone book."""
        principal = principal_for(
            org_id,
            [
                "crm.lead.lead.read:ORG",
                "crm.lead.lead.write:ORG",
                "crm.lead.lead.convert:ORG",
            ],
            subject_ref=data["owner_a"],
            amr=STEPPED_UP,
        )
        with as_principal(principal):
            for field in ("phone", "email"):
                response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": field})
                assert response.status_code == 403, field
                _assert_no_plaintext(response)

    async def test_the_permission_without_the_step_up_is_forbidden(
        self, client, org_id, data
    ) -> None:
        """An ordinary session that has not stepped up must not open the phone book, whatever
        its scope."""
        for amr in ([], ["pwd"]):
            principal = principal_for(org_id, [f"{REVEAL}:ORG"], subject_ref=None, amr=amr)
            with as_principal(principal):
                response = await client.post(
                    _reveal_path(data["lead_a_id"]), json={"field": "phone"}
                )
            assert response.status_code == 403, amr
            assert response.json()["detail"] == "Additional authentication required: otp, stepup"
            _assert_no_plaintext(response)

    async def test_a_session_that_gave_a_code_at_sign_in_is_not_a_step_up(
        self, client, org_id, data, admin_session, redis
    ) -> None:
        """ADR-0023, second review round. Every session opened after an authenticator is set up
        gave a code at sign-in and so carries `otp` on every token for up to seven days. That is
        not a code proved for *this* reveal: the route also requires the marker only
        `POST /auth/mfa/step-up` mints, so a stolen session token opens nothing."""
        for amr, missing in (
            (LOGIN_OTP, "stepup"),
            (["otp"], "stepup"),
            (["pwd", "stepup"], "otp"),
        ):
            principal = principal_for(org_id, [f"{REVEAL}:ORG"], subject_ref=None, amr=amr)
            with as_principal(principal):
                for field in ("phone", "email"):
                    response = await client.post(
                        _reveal_path(data["lead_a_id"]), json={"field": field}
                    )
                    assert response.status_code == 403, (amr, field)
                    assert response.json()["detail"] == (
                        f"Additional authentication required: {missing}"
                    )
                    _assert_no_plaintext(response)
        # Refused before anything was spent or read.
        assert redis.store == {}
        assert await _sensitive_read_count(admin_session, org_id) == 0

    async def test_a_step_up_token_is_accepted(self, client, org_id, data) -> None:
        for amr in (STEPPED_UP, ["pwd", "otp", "stepup"]):
            principal = principal_for(org_id, [f"{REVEAL}:ORG"], subject_ref=None, amr=amr)
            with as_principal(principal):
                response = await client.post(
                    _reveal_path(data["lead_a_id"]), json={"field": "phone"}
                )
            assert response.status_code == 200, amr

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, [f"{REVEAL}:ORG"], subject_ref=None, entitlements=[], amr=STEPPED_UP
        )
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 402
        _assert_no_plaintext(response)

    async def test_a_bad_body_from_an_unauthorised_caller_is_still_forbidden(
        self, client, org_id, data
    ) -> None:
        """The permission and step-up are decided before the body is looked at, so a caller
        who may not reveal learns nothing about what the route accepts."""
        principal = principal_for(org_id, [f"{REVEAL}:ORG"], subject_ref=None)  # no step-up
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "nope"})
        assert response.status_code == 403

    async def test_a_lead_outside_own_scope_is_forbidden(
        self, client, org_id, data, admin_session
    ) -> None:
        """A rep may reveal their own leads, not a colleague's — even though `lead.read:ORG`
        lets them list it. The scope is re-applied to the one lead, not only to the list."""
        principal = principal_for(
            org_id, [f"{REVEAL}:OWN"], subject_ref=data["owner_b"], amr=STEPPED_UP
        )
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 403
        assert response.json()["detail"] == "Out of scope for this resource"
        _assert_no_plaintext(response)
        assert await _sensitive_read_count(admin_session, org_id) == 0

    async def test_an_own_grant_reveals_a_lead_the_caller_owns(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, [f"{REVEAL}:OWN"], subject_ref=data["owner_a"], amr=STEPPED_UP
        )
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 200
        assert response.json()["value"] == RAW_PHONE

    async def test_an_org_grant_reveals_a_colleagues_lead(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, [f"{REVEAL}:ORG"], subject_ref=data["owner_b"], amr=STEPPED_UP
        )
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "email"})
        assert response.status_code == 200
        assert response.json()["value"] == RAW_EMAIL

    async def test_team_and_location_grants_are_refused_rather_than_widened(
        self, client, org_id, data
    ) -> None:
        """`max_scope` is a ceiling, not an allowed-set (ADR-0007 §1), so a TEAM or LOCATION
        grant of the permission can exist. This product defines neither rung for a lead
        (`app/scope.py`), so the object check refuses it instead of reading it as ORG."""
        for scope in ("TEAM", "LOCATION"):
            principal = principal_for(
                org_id,
                [f"{REVEAL}:{scope}"],
                subject_ref=data["owner_a"],
                locations=["BLR"],
                amr=STEPPED_UP,
            )
            with as_principal(principal):
                response = await client.post(
                    _reveal_path(data["lead_a_id"]), json={"field": "phone"}
                )
            assert response.status_code == 403, scope
            _assert_no_plaintext(response)

    async def test_another_organizations_lead_is_not_found(
        self, client, org_id, data, admin_session
    ) -> None:
        """Naming a lead that belongs to a different tenant is the same 404 as naming one that
        does not exist at all — the route confirms nothing about ids outside the caller's
        organization, at the widest scope there is."""
        other_org = uuid.uuid4()
        stranger = principal_for(
            other_org, [f"{REVEAL}:ORG"], subject_ref=uuid.uuid4(), amr=STEPPED_UP
        )
        with as_principal(stranger):
            foreign = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
            unknown = await client.post(_reveal_path(uuid.uuid4()), json={"field": "phone"})

        assert foreign.status_code == 404
        assert unknown.status_code == 404
        assert foreign.json()["detail"] == unknown.json()["detail"]
        assert foreign.json()["detail"] == "That lead is not part of this organization"
        _assert_no_plaintext(foreign)
        # Neither tenant has a "revealed" row to show for it.
        assert await _sensitive_read_count(admin_session, org_id) == 0
        assert await _sensitive_read_count(admin_session, other_org) == 0

    async def test_a_soft_deleted_lead_is_not_found(
        self, client, org_id, data, admin_session
    ) -> None:
        """Deleted means gone: the reveal must not be a way to read a lead a rep removed."""
        async with admin_session.begin():
            await admin_session.execute(
                text("UPDATE lead.leads SET deleted_at = now() WHERE id = :id"),
                {"id": data["lead_a_id"]},
            )
        principal = principal_for(org_id, [f"{REVEAL}:ORG"], amr=STEPPED_UP)
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 404
        _assert_no_plaintext(response)

    async def test_only_phone_and_email_can_be_asked_for(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [f"{REVEAL}:ORG"], amr=STEPPED_UP)
        bodies: list[dict | None] = [
            {"field": "phone_enc"},
            {"field": "notes"},
            {"field": ""},
            {"field": None},
            {},
            {"field": "phone", "lead_id": str(data["lead_a_id"])},  # unknown key on a write
        ]
        with as_principal(principal):
            for body in bodies:
                response = await client.post(_reveal_path(data["lead_a_id"]), json=body)
                assert response.status_code == 422, body
                _assert_no_plaintext(response)
            malformed_id = await client.post(
                "/api/v1/leads/not-a-uuid:reveal", json={"field": "phone"}
            )
        assert malformed_id.status_code == 422

    async def test_the_route_is_post_only(self, client, org_id, data) -> None:
        """A GET would be cacheable, prefetchable and loggable in a query string — the reason
        the reveal is a POST. Nothing answers it as one."""
        principal = principal_for(
            org_id, ["crm.lead.lead.read:ORG", f"{REVEAL}:ORG"], amr=STEPPED_UP
        )
        with as_principal(principal):
            response = await client.get(_reveal_path(data["lead_a_id"]))
        assert response.status_code in {404, 405, 422}
        _assert_no_plaintext(response)

    async def test_refusals_leave_no_sensitive_read_audit_row(
        self, client, org_id, data, admin_session
    ) -> None:
        refused = [
            principal_for(org_id, [], subject_ref=data["owner_a"], amr=STEPPED_UP),
            principal_for(org_id, [f"{REVEAL}:ORG"], subject_ref=data["owner_a"]),
            principal_for(org_id, [f"{REVEAL}:OWN"], subject_ref=data["owner_b"], amr=STEPPED_UP),
        ]
        for principal in refused:
            with as_principal(principal):
                response = await client.post(
                    _reveal_path(data["lead_a_id"]), json={"field": "phone"}
                )
            assert response.status_code == 403
        assert await _sensitive_read_count(admin_session, org_id) == 0


class TestRevealOwnScopeIsKeyedOnSubjectRef:
    """Pins what the OWN rung means for a lead **today**, so a change is deliberate.

    `ctx.assert_can_touch` compares `Lead.owner_id` with `principal.subject_ref` — the token's
    `sref` claim, which auth sources from `memberships.employee_id` (ADR-0008). `Lead.owner_id`
    is an id from customer-service's rep directory (`owners:validate`), a UUID generated when
    the rep was added there. Nothing in the platform gives the two the same value, so in a
    tenant that has not deliberately written a CRM owner id into `employee_id` (the
    "documented abuse" in ADR-0008's Consequences), an OWN grant matches nothing.

    ADR-0008 Q5 decides the direction — CRM keys OWN on the membership instead — and assigns it
    to a later slice (C15). When that lands these tests are the ones that must change, on
    purpose.
    """

    async def test_own_matches_when_subject_ref_equals_the_owner_id(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, [f"{REVEAL}:OWN"], subject_ref=data["owner_a"], amr=STEPPED_UP
        )
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 200

    async def test_an_unlinked_principal_cannot_reveal_even_a_lead_it_owns(
        self, client, org_id, data
    ) -> None:
        """No `sref` — a membership with no employee link, which is every principal in a CRM
        tenant without HRMS — means OWN has nothing to compare, so it refuses every lead."""
        principal = principal_for(org_id, [f"{REVEAL}:OWN"], subject_ref=None, amr=STEPPED_UP)
        with as_principal(principal):
            for lead_id in (data["lead_a_id"], data["lead_b_id"]):
                response = await client.post(_reveal_path(lead_id), json={"field": "phone"})
                assert response.status_code == 403, lead_id
                _assert_no_plaintext(response)

    async def test_the_membership_id_is_not_what_own_compares(self, client, org_id, data) -> None:
        """A principal whose *membership id* equals the lead's owner id, but whose `sref`
        does not, is refused: today's OWN is the employee link, not the membership."""
        principal = principal_for(
            org_id, [f"{REVEAL}:OWN"], subject_ref=uuid.uuid4(), amr=STEPPED_UP
        )
        principal = principal.model_copy(update={"membership_id": data["owner_a"]})
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 403

    async def test_an_org_grant_needs_no_subject_ref(self, client, org_id, data) -> None:
        """The other side of the same fact: ORG never consults `sref`, so an administrator
        without an employee link reveals normally. This is the path that works everywhere."""
        principal = principal_for(org_id, [f"{REVEAL}:ORG"], subject_ref=None, amr=STEPPED_UP)
        with as_principal(principal):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 200
        assert response.json()["value"] == RAW_PHONE
