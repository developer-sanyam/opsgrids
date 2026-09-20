"""Negative-authz for the routing endpoints (ADR-0003 §3, ADR-0006 §11) and cross-tenant
isolation: every new route refuses the caller it should, a request only 404s (never 403) when it
cannot be seen, and another organization's rows are invisible to the inbox, the list and the
detail alike — even when queried by an admin-bypassed session with the same ids reused in both
tenants (data model §6's own seeding discipline).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import open_request, principal_for, seed_person

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- endpoint-level authz


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id: uuid.UUID) -> None:
        response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 401

    async def test_inbox_requires_the_decide_permission(self, client, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_requests_list_requires_the_read_permission(
        self, client, org_id: uuid.UUID
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/requests")
        assert response.status_code == 403

    async def test_decision_requires_the_decide_permission(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, manager, [])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "approve"}
            )
        assert response.status_code == 403


# --------------------------------------------------------------------- decision object-level


class TestDecisionObjectLevel:
    async def test_a_caller_not_on_the_chain_is_refused(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, bystander = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, bystander, ["core.approval.request.decide:OWN"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "approve"}
            )
        assert response.status_code == 403

    async def test_the_requester_is_refused_even_holding_decide_org(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, requester, ["core.approval.request.decide:ORG"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "approve"}
            )
        assert response.status_code == 403

    async def test_deciding_twice_is_a_conflict_not_a_second_success(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, manager, ["core.approval.request.decide:OWN"])
        with as_principal(principal):
            first = await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "approve"}
            )
            second = await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "reject"}
            )
        assert first.status_code == 200
        assert second.status_code == 409

    async def test_a_decider_can_still_read_what_they_decided(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The read widens to a past participant, any outcome (ADR-0006 §11 amended) — the
        decider keeps full read access to a request that is no longer pending."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, manager, ["core.approval.request.decide:OWN"])
        with as_principal(principal):
            await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "approve"}
            )

        reader = principal_for(org_id, manager, ["core.approval.request.read:OWN"])
        with as_principal(reader):
            response = await client.get(f"/api/v1/approvals/requests/{request.id}")
        assert response.status_code == 200
        assert response.json()["status"] == "approved"


class TestExitedOrgHolder:
    """ADR-0006 §9's negative-authz case: permission revocation can lag an `exited` status
    landing on the copy, so `decide:ORG`'s route (c) — unassigned or abandoned steps — must
    refuse a caller whose own node is `exited`, exactly as routes (a) and (b) already did, both
    at decision and in the inbox listing that would otherwise hand them `request_note`.
    """

    async def test_an_exited_org_holder_is_refused_at_decision(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, exited_org_holder = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, exited_org_holder, status="exited")
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None  # requester has no hierarchy row -> unassigned step

        principal = principal_for(org_id, exited_org_holder, ["core.approval.request.decide:ORG"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "approve"}
            )
        assert response.status_code == 403

    async def test_an_exited_org_holder_sees_nothing_in_the_inbox(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, exited_org_holder = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, exited_org_holder, status="exited")
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, exited_org_holder, ["core.approval.request.decide:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_an_active_org_holder_still_sees_and_decides_the_unassigned_step(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The positive case the fix must not break: a caller with no hierarchy row at all reads
        as not-exited (`caller_active`'s fail-open reading, ADR-0006 §4) and still reaches and
        decides an unassigned step exactly as before."""
        requester, org_holder = uuid.uuid4(), uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, org_holder, ["core.approval.request.decide:ORG"])
        with as_principal(principal):
            inbox = await client.get("/api/v1/approvals/inbox")
            decision = await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "approve"}
            )
        assert inbox.status_code == 200
        assert {i["id"] for i in inbox.json()["items"]} == {str(request.id)}
        assert decision.status_code == 200


# --------------------------------------------------------------------- read visibility


class TestRequestListVisibility:
    async def test_own_scope_sees_only_requests_it_raised(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        alice, bob, manager = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, alice, reporting_manager_id=manager)
            await seed_person(session, org_id, bob, reporting_manager_id=manager)
        alice_request = await open_request(session, org_id, requester_employee_id=alice)
        bob_request = await open_request(session, org_id, requester_employee_id=bob)
        assert alice_request is not None
        assert bob_request is not None

        principal = principal_for(org_id, alice, ["core.approval.request.read:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/requests")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {str(alice_request.id)}

    async def test_org_scope_sees_every_request(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        alice, bob, manager = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, alice, reporting_manager_id=manager)
            await seed_person(session, org_id, bob, reporting_manager_id=manager)
        alice_request = await open_request(session, org_id, requester_employee_id=alice)
        bob_request = await open_request(session, org_id, requester_employee_id=bob)
        assert alice_request is not None
        assert bob_request is not None

        principal = principal_for(org_id, uuid.uuid4(), ["core.approval.request.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/requests")
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {str(alice_request.id), str(bob_request.id)}

    async def test_team_scope_sees_only_direct_reports_requests(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`core.approval.request.read`'s `max_scope` is ORG (`tests/test_chain.py`'s own note on
        this for `/chain`), so a TEAM grant is legal here and must be honoured for real — the
        fix mirrors slice 1's for `/chain`, this time reached through the requester's row on the
        hierarchy copy rather than the single subject a detail endpoint compares against."""
        manager, report, someone_elses_report, other_manager = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, report, reporting_manager_id=manager)
            await seed_person(session, org_id, other_manager)
            await seed_person(
                session, org_id, someone_elses_report, reporting_manager_id=other_manager
            )
        report_request = await open_request(session, org_id, requester_employee_id=report)
        other_request = await open_request(
            session, org_id, requester_employee_id=someone_elses_report
        )
        assert report_request is not None
        assert other_request is not None

        principal = principal_for(org_id, manager, ["core.approval.request.read:TEAM"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/requests")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {str(report_request.id)}

    async def test_team_scope_excludes_a_requester_the_copy_has_not_seen(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A requester with no `hierarchy_nodes` row (`requester_not_in_hierarchy`, ADR-0006 §4)
        has no manager to compare against, so TEAM must fail closed on that row rather than treat
        the missing join as a match — the same "fail closed" reading `/chain`'s own TEAM test
        gives an unknown subject."""
        manager, unseen_requester = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
        request = await open_request(session, org_id, requester_employee_id=unseen_requester)
        assert request is not None

        principal = principal_for(org_id, manager, ["core.approval.request.read:TEAM"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/requests")
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_location_scope_sees_only_requests_at_the_callers_location(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        blr_requester, mum_requester = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, blr_requester, location_code="BLR")
            await seed_person(session, org_id, mum_requester, location_code="MUM")
        blr_request = await open_request(session, org_id, requester_employee_id=blr_requester)
        mum_request = await open_request(session, org_id, requester_employee_id=mum_requester)
        assert blr_request is not None
        assert mum_request is not None

        principal = principal_for(
            org_id, uuid.uuid4(), ["core.approval.request.read:LOCATION"], locations=["BLR"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/requests")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {str(blr_request.id)}


class TestRequestListMineOnlyFilter:
    """`?requester=me` (the "My requests" screen's own filter) layers on top of scope rather than
    replacing it: scope answers *may* the caller see a row, not whether it is theirs — an ORG or
    TEAM/LOCATION reader legitimately sees other people's requests through this same endpoint, so
    without this filter "my requests" would render as "everyone's requests" for anyone above OWN.
    """

    async def test_org_scope_with_mine_only_sees_only_the_callers_own_requests(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        alice, bob, manager = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, alice, reporting_manager_id=manager)
            await seed_person(session, org_id, bob, reporting_manager_id=manager)
        alice_request = await open_request(session, org_id, requester_employee_id=alice)
        bob_request = await open_request(session, org_id, requester_employee_id=bob)
        assert alice_request is not None
        assert bob_request is not None

        principal = principal_for(org_id, alice, ["core.approval.request.read:ORG"])
        with as_principal(principal):
            mine = await client.get("/api/v1/approvals/requests", params={"requester": "me"})
            everyone = await client.get("/api/v1/approvals/requests")
        assert mine.status_code == 200
        mine_body = mine.json()
        assert {item["id"] for item in mine_body["items"]} == {str(alice_request.id)}
        # Paging/total agree with the filtered set, not the unfiltered scope — an admin paging
        # "my requests" must not see a `total` that counts everyone else's rows too.
        assert mine_body["total"] == 1
        # Unfiltered ORG still reaches both — the filter narrows, it does not replace scope.
        everyone_ids = {item["id"] for item in everyone.json()["items"]}
        assert everyone_ids == {str(alice_request.id), str(bob_request.id)}

    async def test_own_scope_is_unaffected_by_mine_only(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """An OWN-scoped caller already sees only their own requests — `?requester=me` must be a
        no-op for them, not an additional restriction that could ever narrow OWN further."""
        alice, bob, manager = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, alice, reporting_manager_id=manager)
            await seed_person(session, org_id, bob, reporting_manager_id=manager)
        alice_request = await open_request(session, org_id, requester_employee_id=alice)
        await open_request(session, org_id, requester_employee_id=bob)
        assert alice_request is not None

        principal = principal_for(org_id, alice, ["core.approval.request.read:OWN"])
        with as_principal(principal):
            mine = await client.get("/api/v1/approvals/requests", params={"requester": "me"})
            unfiltered = await client.get("/api/v1/approvals/requests")
        assert mine.status_code == 200
        assert unfiltered.status_code == 200
        mine_ids = {item["id"] for item in mine.json()["items"]}
        unfiltered_ids = {item["id"] for item in unfiltered.json()["items"]}
        assert mine_ids == unfiltered_ids == {str(alice_request.id)}

    async def test_a_caller_with_no_subject_ref_gets_nothing_for_mine_only(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The same "nothing to own with" shape `ScopeContext.apply`'s own `OWN` branch uses for a
        membership with no employee link — matches nothing rather than rendering `col == NULL`,
        which would quietly return every request with no requester link at all."""
        requester = uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, None, ["core.approval.request.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/requests", params={"requester": "me"})
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_a_client_supplied_requester_id_is_rejected(
        self, client, org_id: uuid.UUID
    ) -> None:
        """The only accepted value is the literal `"me"`, resolved server-side from the caller's
        own `subject_ref` — there is no `requester=<uuid>` shape for application code to trust or
        distrust, because FastAPI's own query validation refuses anything outside `Literal["me"]`
        before a route function ever runs."""
        principal = principal_for(org_id, uuid.uuid4(), ["core.approval.request.read:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/requests", params={"requester": str(uuid.uuid4())}
            )
        assert response.status_code == 422


class TestDetailVisibility:
    async def test_an_uninvolved_own_reader_gets_404_not_403(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, bystander = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, bystander, ["core.approval.request.read:OWN"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/approvals/requests/{request.id}")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_an_unknown_id_is_also_404(self, client, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["core.approval.request.read:ORG"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/approvals/requests/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_the_requester_can_read_their_own_request(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, requester, ["core.approval.request.read:OWN"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/approvals/requests/{request.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["requester_employee_id"] == str(requester)
        # request_note is present on the detail for anyone entitled to reach it (ADR-0006 §3).
        assert body["request_note"]


# --------------------------------------------------------------------- cross-tenant isolation


class TestCrossTenantIsolation:
    async def test_a_request_opened_for_another_organization_never_appears_in_the_inbox(
        self,
        client,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """Same employee id reused across both organizations on purpose (data model §6's own
        seeding discipline) — isolation must hold on `organization_id`, not merely on the id
        happening to differ."""
        shared_employee_id = uuid.uuid4()
        manager = uuid.uuid4()
        async with admin_session.begin():
            await seed_person(admin_session, other_org, manager)
            await seed_person(
                admin_session, other_org, shared_employee_id, reporting_manager_id=manager
            )
        async with admin_session.begin():
            other_request = await services.open_regularization_request(
                admin_session,
                organization_id=other_org,
                source_ref=uuid.uuid4(),
                requester_employee_id=shared_employee_id,
                summary={"date": "2026-09-12", "from": "absent", "to": "present"},
                request_note="Other tenant's request",
            )
        assert other_request is not None

        # `manager` also exists (unrelated) in `org_id`, and holds both grants there at ORG.
        async with session.begin():
            await seed_person(session, org_id, manager)

        principal = principal_for(
            org_id,
            manager,
            ["core.approval.request.decide:ORG", "core.approval.request.read:ORG"],
        )
        with as_principal(principal):
            inbox = await client.get("/api/v1/approvals/inbox")
            requests_list = await client.get("/api/v1/approvals/requests")
            detail = await client.get(f"/api/v1/approvals/requests/{other_request.id}")

        assert inbox.status_code == 200
        assert other_request.id not in {uuid.UUID(i["id"]) for i in inbox.json()["items"]}
        assert requests_list.status_code == 200
        assert other_request.id not in {uuid.UUID(i["id"]) for i in requests_list.json()["items"]}
        assert detail.status_code == 404

    async def test_deciding_a_cross_tenant_request_id_is_refused(
        self,
        client,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        manager, requester = uuid.uuid4(), uuid.uuid4()
        async with admin_session.begin():
            await seed_person(admin_session, other_org, manager)
            await seed_person(admin_session, other_org, requester, reporting_manager_id=manager)
        async with admin_session.begin():
            other_request = await services.open_regularization_request(
                admin_session,
                organization_id=other_org,
                source_ref=uuid.uuid4(),
                requester_employee_id=requester,
                summary={"date": "2026-09-12", "from": "absent", "to": "present"},
                request_note="Other tenant's request",
            )
        assert other_request is not None

        principal = principal_for(org_id, manager, ["core.approval.request.decide:ORG"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/approvals/requests/{other_request.id}/decision",
                json={"outcome": "approve"},
            )
        assert response.status_code == 404
