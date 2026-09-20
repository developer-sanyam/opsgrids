"""ADR-0008: account owns `memberships.employee_id`, and this service's half of that decision.

Two things are under test here, and they are two halves of one rule — "employee does not own the
link":

1. **The freeze.** `EmployeeIn.membership_id` is still declared and is now refused with a
   `BusinessRuleProblem` that names account's endpoint (ADR-0008 §3). The field stays declared on
   purpose, and `TestTheFieldMustStayDeclared` pins the reason: with no `model_config` on the
   model, Pydantic's default `extra="ignore"` would swallow a supplied `membership_id` silently
   if the field were deleted, turning a loud refusal into a value that vanishes.
2. **The read.** `GET /internal/v1/employee-links` — two UUIDs per row for *every* employee,
   linked or not, because one page serves both halves of the reconcile (ADR-0008 §8) — and the
   security posture it tightens: the `org` claim is **required**, not checked-when-present, and
   its issuer allowlist is its own rather than shared with `/internal/v1/hierarchy`.

The two assertions to keep whatever else changes here: nothing but the two ids ever reaches the
response (the `not in response.text` checks, which catch a selector that loads whole entities),
and an unlinked employee is **present with a null** rather than absent — the branch verify has to
tell apart from an id that does not exist at all.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.main import app
from app.models import AuditLog, Employee
from app.schemas import EmployeeIn
from platform_core.auth import Principal, Scope, get_principal
from platform_core.errors import BusinessRuleProblem
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

#: The issuer the reconcile mints as (`app/api/internal.py::EMPLOYEE_LINKS_ISSUERS`).
RECONCILE = "reconcile-membership-links"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    """Same override `test_authz.py` uses — the gateway's forwarded principal, without a gateway."""
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def reconcile_headers(
    *,
    audience: str = "employee",
    caller: str = RECONCILE,
    org: uuid.UUID | None = None,
) -> dict[str, str]:
    """A service token shaped like the reconcile's.

    `org` is a keyword with no default on purpose in every *positive* test below: the whole point
    of this endpoint's posture is that the claim is bound, so a helper that supplied it
    automatically would make the "missing claim is refused" test impossible to write honestly.
    """
    claims = {"org": str(org)} if org is not None else None
    return {
        "X-Internal-Token": mint_service_token(
            settings(), audience=audience, caller=caller, claims=claims
        ),
        "X-Calling-Service": caller,
    }


async def _link(session: AsyncSession, employee: Employee, membership_id: uuid.UUID) -> None:
    """Set `membership_id` on an existing row directly.

    Deliberately not through the service: the write path refuses this field now, which is the
    other half of what this module tests. These rows stand in for the pre-ADR-0008 data the
    reconcile exists to find — the estate's only record of anybody's intent.
    """
    async with session.begin():
        await session.execute(
            text("UPDATE employee.employees SET membership_id = :m WHERE id = :id"),
            {"m": membership_id, "id": employee.id},
        )


# ------------------------------------------------------------------ the frozen create field


class TestCreateRefusesMembershipId:
    async def test_supplying_membership_id_is_refused_and_names_account_s_endpoint(
        self, session: AsyncSession, org_id
    ) -> None:
        """Refused, not ignored — and the message has to be actionable, because a caller that is
        told "no" without being told "go there instead" is a support ticket (ADR-0008 §3)."""
        ctx = ctx_for(org_id, Employee(id=uuid.uuid4()), "hrms.people.employee.write", Scope.ORG)
        with pytest.raises(BusinessRuleProblem) as excinfo:
            async with session.begin():
                await services.create_employee(
                    session,
                    ctx,
                    EmployeeIn(
                        employee_no="EMP9001",
                        full_name="Linked Larry",
                        membership_id=uuid.uuid4(),
                    ),
                )
        message = str(excinfo.value)
        assert "membership_id" in message
        assert "PATCH /api/v1/members/{membership_id}" in message

    async def test_the_refusal_is_a_422_over_http_and_writes_no_row(
        self, client, session: AsyncSession, org_id, people
    ) -> None:
        """End to end, by a caller who holds everything they need. The refusal is a business rule
        and not an authorization failure — a writer with `write:ORG` is refused on the merits —
        and a rejected request is not a change: no employee row lands (ADR-0008 §10).
        """
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/employees",
                json={
                    "employee_no": "EMP9002",
                    "full_name": "Linked Larry",
                    "membership_id": str(uuid.uuid4()),
                },
            )
        assert response.status_code == 422
        assert "PATCH /api/v1/members/{membership_id}" in response.text

        async with session.begin():
            count = (
                await session.execute(
                    text("SELECT count(*) FROM employee.employees WHERE employee_no = :n"),
                    {"n": "EMP9002"},
                )
            ).scalar_one()
        assert count == 0

    async def test_a_create_without_membership_id_still_works(
        self, session: AsyncSession, org_id
    ) -> None:
        """The negative control. The freeze must refuse exactly one thing and nothing else — a
        guard that also broke ordinary onboarding would be caught here rather than in production.
        """
        ctx = ctx_for(org_id, Employee(id=uuid.uuid4()), "hrms.people.employee.write", Scope.ORG)
        async with session.begin():
            created = await services.create_employee(
                session,
                ctx,
                EmployeeIn(employee_no="EMP9003", full_name="Unlinked Ursula"),
            )
        assert created.employee_no == "EMP9003"
        assert created.membership_id is None

    async def test_an_explicit_null_membership_id_is_not_refused(
        self, session: AsyncSession, org_id
    ) -> None:
        """`membership_id: null` states the field's own default. Refusing it would fail an
        integration that sends a complete body with nulls, for no gain: the rule is "this service
        does not *write* the link", and writing null is not writing the link."""
        ctx = ctx_for(org_id, Employee(id=uuid.uuid4()), "hrms.people.employee.write", Scope.ORG)
        async with session.begin():
            created = await services.create_employee(
                session,
                ctx,
                EmployeeIn(employee_no="EMP9004", full_name="Null Nadia", membership_id=None),
            )
        assert created.membership_id is None


class TestTheFieldMustStayDeclared:
    def test_the_model_ignores_unknown_fields_so_deleting_the_field_would_be_silent(self) -> None:
        """The reason ADR-0008 §3 keeps the field instead of deleting it, pinned as a test rather
        than trusted as a comment.

        `EmployeeIn` sets no `model_config`, so Pydantic's default `extra="ignore"` applies:
        anything not declared is dropped during validation with no error. Delete
        `membership_id` and a caller that has been sending it gets a 201 and a record with no
        link — the exact silent failure the ADR exists to remove, wearing a different hat.

        If this test ever fails because the model gained `extra="forbid"`, the ADR's reasoning
        changes and the refusal could move into validation. Change that deliberately, not by
        accident.
        """
        assert EmployeeIn.model_config.get("extra") in (None, "ignore")

        parsed = EmployeeIn.model_validate(
            {
                "employee_no": "EMP9005",
                "full_name": "Extra Eddie",
                "not_a_declared_field": str(uuid.uuid4()),
            }
        )
        assert not hasattr(parsed, "not_a_declared_field")

        # And the field is still declared, which is what makes the refusal reachable at all.
        assert "membership_id" in EmployeeIn.model_fields


# ------------------------------------------------------------------ the internal links endpoint


class TestEmployeeLinksAuth:
    async def test_it_refuses_a_request_with_no_service_token(self, client, org_id, people) -> None:
        response = await client.get(
            "/internal/v1/employee-links", params={"organization_id": str(org_id)}
        )
        assert response.status_code == 401

    async def test_a_token_with_no_org_claim_is_refused(self, client, org_id, people) -> None:
        """**The tightening.** On `/internal/v1/hierarchy` a token with no `org` claim is
        accepted and trusted for whichever organization the query string names; here it is
        refused. The claim is required from day one because the only caller binds it, so there is
        no installed base to break (ADR-0008 §8).

        This is the single most important assertion in this module: it is the difference between
        "the caller proved which tenant it was authorized for" and "the caller told us which
        tenant to open".
        """
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(),
        )
        assert response.status_code == 401

    async def test_the_sibling_endpoint_still_accepts_a_token_with_no_org_claim(
        self, client, org_id, people
    ) -> None:
        """The contrast that makes the previous test a *tightening* rather than a coincidence:
        the same shape of token, missing the same claim, is still accepted by `/hierarchy`
        because approval's real client does not bind it yet. If this ever starts failing,
        `/hierarchy` has been tightened too and its `require_org` flag should be flipped
        deliberately.
        """
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(caller="approval"),
        )
        assert response.status_code == 200

    async def test_a_token_whose_org_claim_does_not_match_the_query_is_refused(
        self, client, org_id, people
    ) -> None:
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=uuid.uuid4()),
        )
        assert response.status_code == 401

    async def test_a_token_from_a_foreign_issuer_is_refused(self, client, org_id, people) -> None:
        """Right audience, right signature, unexpired, `org` claim bound correctly — and an `iss`
        outside `ALLOWED_INTERNAL_ISSUERS` entirely."""
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(caller="billing", org=org_id),
        )
        assert response.status_code == 401

    async def test_approvals_token_cannot_read_the_links_endpoint(
        self, client, org_id, people
    ) -> None:
        """Containment between the two endpoints' caller sets. `approval` clears the router-level
        union but is not in `EMPLOYEE_LINKS_ISSUERS`, so it is refused here — the allowlist is per
        route precisely so one endpoint's legitimate caller does not silently become the other's.
        """
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(caller="approval", org=org_id),
        )
        assert response.status_code == 401

    async def test_the_reconcile_token_cannot_read_the_hierarchy_endpoint(
        self, client, org_id, people
    ) -> None:
        """The mirror image, and the one that actually protects PII: adding the reconcile to this
        service's internal callers must not hand it the hierarchy endpoint's names, employee
        numbers and reporting lines."""
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=org_id),
        )
        assert response.status_code == 401

    async def test_a_token_for_another_service_is_refused(self, client, org_id, people) -> None:
        """Wrong `aud` — refused by the signature/audience check before `iss` is even consulted."""
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(audience="billing", org=org_id),
        )
        assert response.status_code == 401

    async def test_it_is_absent_from_the_public_schema(self, client, org_id, people) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert not [p for p in schema["paths"] if p.startswith("/internal")]
        assert "/internal/v1/employee-links" not in schema["paths"]


class TestEmployeeLinksPage:
    async def test_it_returns_two_ids_per_employee_and_nothing_else(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        """The whole contract: every employee comes back as exactly `{employee_id,
        membership_id}` and nothing else travels — no name, no employee number, no status. The
        `not in response.text` assertions are the ones that matter: a response model that merely
        *declares* two fields would still be a leak if the selector loaded whole entities and
        something later serialised them."""
        membership = uuid.uuid4()
        await _link(session, people["report"], membership)

        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=org_id),
        )
        assert response.status_code == 200
        body = response.json()

        assert body["next_cursor"] is None
        by_employee = {i["employee_id"]: i["membership_id"] for i in body["items"]}
        assert by_employee == {
            str(people["manager"].id): None,
            str(people["other_manager"].id): None,
            str(people["report"].id): str(membership),
            str(people["outsider"].id): None,
        }
        # No PII, no directory data, not even the low-sensitivity kind.
        assert "Rhea Report" not in response.text
        assert "EMP0003" not in response.text
        assert "MUM" not in response.text

    async def test_an_unlinked_employee_appears_with_a_null_membership_id(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        """The case a linked-only page would lose, and the reason it must not.

        ADR-0008 §8's *verify* half treats any `employee_id` account holds that is **absent** from
        this page as an id employee does not recognise — §4's mistyped UUID. So an employee that
        is real but simply not linked yet has to be *present with a null*, not missing: filtered
        out, it would be indistinguishable from a typo, and during a rollout that population is
        precisely the one the reconcile exists to create. A null here is payload, not padding.
        """
        await _link(session, people["report"], uuid.uuid4())

        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=org_id),
        )
        assert response.status_code == 200
        items = response.json()["items"]

        unlinked = [i for i in items if i["membership_id"] is None]
        assert {i["employee_id"] for i in unlinked} == {
            str(people["manager"].id),
            str(people["other_manager"].id),
            str(people["outsider"].id),
        }
        # Present and null, not absent — stated as its own assertion because "absent" and
        # "present with a null" are the two branches verify has to tell apart.
        assert str(people["manager"].id) in {i["employee_id"] for i in items}

    async def test_every_item_carries_exactly_the_two_keys(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        await _link(session, people["manager"], uuid.uuid4())
        await _link(session, people["report"], uuid.uuid4())

        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=org_id),
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 4
        for item in items:
            # Both keys always present. `membership_id` may be null — that is the unlinked case,
            # and it is information the reconcile needs rather than a row to skip.
            assert set(item) == {"employee_id", "membership_id"}
            assert item["employee_id"] is not None

    async def test_an_organization_with_no_employees_gets_an_empty_terminated_page(
        self, client, org_id
    ) -> None:
        """No `people` fixture here: with the page no longer filtered to linked rows, an empty
        page means an organization with no employees at all. Empty must terminate the walk rather
        than loop it.

        (ADR-0008 §8 predicts `--dry-run` finds *zero links* on real tenants, since nothing in the
        platform has ever sent `membership_id` on create. That is now a page full of nulls rather
        than an empty page — which is the point: the reconcile still learns which employees
        exist.)"""
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=org_id),
        )
        assert response.status_code == 200
        assert response.json() == {"items": [], "next_cursor": None}

    async def test_a_tenant_with_employees_but_no_links_returns_them_all_with_nulls(
        self, client, org_id, people
    ) -> None:
        """ADR-0008 §8's predicted `--dry-run` shape on a real tenant: `links_written: 0`, but a
        full page — so verify recognises every id account holds and reports nothing as junk."""
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=org_id),
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert {i["employee_id"] for i in items} == {str(e.id) for e in people.values()}
        assert all(i["membership_id"] is None for i in items)

    async def test_a_successful_page_writes_an_audit_row(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        await _link(session, people["report"], uuid.uuid4())

        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=org_id),
        )
        assert response.status_code == 200

        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "employee.links.exported",
                    )
                )
            ).scalar_one()
        assert row.entity == "employee_link_page"
        assert row.entity_id is None
        assert row.after is not None
        assert row.after["caller_iss"] == RECONCILE
        # Every employee, not only the linked one: the count is the size of the page actually
        # read, which is what makes "how much did this caller export" answerable from the trail.
        assert row.after["count"] == len(people)
        assert row.after["cursor"] is None
        assert row.after["limit"] == 500

    async def test_a_refused_call_writes_no_audit_row(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        """The guard runs before the transaction opens, so a refusal leaves no trace in the audit
        trail that a page was read — because none was."""
        await _link(session, people["report"], uuid.uuid4())
        refused = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(),
        )
        assert refused.status_code == 401

        async with session.begin():
            count = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "employee.links.exported")
                )
            ).all()
        assert count == []

    async def test_it_paginates_by_keyset_like_its_sibling(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        """The same boundary behaviour `TestInternalHierarchyEndpoint` pins: a page landing
        exactly on `limit` still carries a `next_cursor`, and it is the following empty page —
        not a short one — that terminates the walk."""
        links = {}
        for key in ("manager", "other_manager", "report", "outsider"):
            membership = uuid.uuid4()
            links[str(people[key].id)] = str(membership)
            await _link(session, people[key], membership)

        first = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id), "limit": 2},
            headers=reconcile_headers(org=org_id),
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["items"]) == 2
        assert first_body["next_cursor"] is not None

        second = await client.get(
            "/internal/v1/employee-links",
            params={
                "organization_id": str(org_id),
                "limit": 2,
                "cursor": first_body["next_cursor"],
            },
            headers=reconcile_headers(org=org_id),
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["items"]) == 2
        # Four links, page size two: this page lands exactly on the boundary, so it still carries
        # a cursor — the caller cannot yet tell it was the last page with data.
        assert second_body["next_cursor"] is not None

        third = await client.get(
            "/internal/v1/employee-links",
            params={
                "organization_id": str(org_id),
                "limit": 2,
                "cursor": second_body["next_cursor"],
            },
            headers=reconcile_headers(org=org_id),
        )
        assert third.status_code == 200
        third_body = third.json()
        assert third_body["items"] == []
        assert third_body["next_cursor"] is None

        seen = {i["employee_id"]: i["membership_id"] for i in first_body["items"]}
        seen |= {i["employee_id"]: i["membership_id"] for i in second_body["items"]}
        assert seen == links

    async def test_the_walk_covers_every_employee_in_id_order_whatever_their_link_state(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        """Paging one row at a time over a mixed population. The cursor is the last returned
        employee id and there is no link filter, so a linked row sitting between two unlinked
        ones (or the reverse) neither skips anybody nor stalls the walk — verify depends on the
        page being the *complete* set of this organization's employee ids, so a walk that dropped
        one would silently manufacture an `unrecognised_employee_id`."""
        by_id = sorted(people.values(), key=lambda e: e.id)
        # Link the second of four, so the page has unlinked rows on both sides of a linked one.
        await _link(session, by_id[1], uuid.uuid4())

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(len(by_id) + 1):
            params: dict[str, object] = {"organization_id": str(org_id), "limit": 1}
            if cursor is not None:
                params["cursor"] = cursor
            page = await client.get(
                "/internal/v1/employee-links",
                params=params,
                headers=reconcile_headers(org=org_id),
            )
            assert page.status_code == 200
            body = page.json()
            seen.extend(i["employee_id"] for i in body["items"])
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert cursor is None, "the walk did not terminate"
        # Every employee, exactly once, in ascending id order.
        assert seen == [str(e.id) for e in by_id]

    async def test_the_page_size_is_capped(self, client, org_id, people) -> None:
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id), "limit": 5000},
            headers=reconcile_headers(org=org_id),
        )
        assert response.status_code == 422

    async def test_it_cannot_return_another_organizations_links(
        self, client, org_id, people, session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        """Two defences, both proved here. The `org` claim means the token names the tenant, so a
        caller cannot ask for another one; RLS means that even if it could, one call never mixes
        two organizations' rows."""
        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Employee.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    employee_no="Z0002",
                    full_name="Zoe Zeta",
                    employment_type="full_time",
                    work_mode="office",
                    status="active",
                    membership_id=uuid.uuid4(),
                )
            )
        await _link(session, people["report"], uuid.uuid4())

        # A token bound to `org_id` asking for `other_org` is refused outright — unlike
        # `/hierarchy`, where a claimless token would simply be answered.
        cross = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(other_org)},
            headers=reconcile_headers(org=org_id),
        )
        assert cross.status_code == 401

        own = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=reconcile_headers(org=org_id),
        )
        assert own.status_code == 200
        # Exactly this organization's employees and no other's — and since the page now carries
        # unlinked rows too, "the other tenant's row is absent" is a claim about the whole
        # population rather than about whichever rows happened to be linked.
        assert {i["employee_id"] for i in own.json()["items"]} == {
            str(e.id) for e in people.values()
        }
        assert "Zoe Zeta" not in own.text
        assert "Z0002" not in own.text
