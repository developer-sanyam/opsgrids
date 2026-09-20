"""`GET /delegate-candidates` (ADR-0006 §11, screens §4.E): the delegation picker's search,
cap and exclusions, plus its own negative-authz and cross-tenant isolation. The query plan is
verified separately, in `tests/test_delegate_candidates_plan.py`.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for, seed_person

pytestmark = pytest.mark.integration

DELEGATION_WRITE = "core.approval.delegation.write"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


class TestSearch:
    async def test_matches_by_name_prefix_case_insensitively(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller, priya, prahlad, rohan = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(session, org_id, priya, display_name="Priya Nair")
            await seed_person(session, org_id, prahlad, display_name="Prahlad Rao")
            await seed_person(session, org_id, rohan, display_name="Rohan Mehta")

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "PRI"}
            )
        assert response.status_code == 200
        names = {c["display_name"] for c in response.json()}
        assert names == {"Priya Nair"}

    async def test_matches_by_employee_number_prefix(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller, candidate = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(
                session, org_id, candidate, display_name="Someone Else", employee_no="STAFF042"
            )

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "STAFF0"}
            )
        assert response.status_code == 200
        ids = {c["employee_id"] for c in response.json()}
        assert ids == {str(candidate)}

    async def test_a_literal_percent_in_the_query_is_not_a_wildcard(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller, candidate = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(session, org_id, candidate, display_name="Anyone At All")

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "50%"}
            )
        assert response.status_code == 200
        assert response.json() == []

    async def test_no_matches_is_an_empty_list_not_an_error(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "zzzzz"}
            )
        assert response.status_code == 200
        assert response.json() == []

    async def test_results_are_capped_at_twenty(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            for i in range(25):
                await seed_person(
                    session,
                    org_id,
                    uuid.uuid4(),
                    display_name=f"Candidate {i:02d}",
                    employee_no=f"CAND{i:02d}",
                )

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "Candidate"}
            )
        assert response.status_code == 200
        assert len(response.json()) == 20

    async def test_q_shorter_than_two_characters_is_rejected(
        self, client, org_id: uuid.UUID
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegate-candidates", params={"q": "p"})
        assert response.status_code == 422

    async def test_a_whitespace_only_q_is_rejected(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The security-major regression: `q="  "` (two spaces) has raw length 2 and used to
        pass `Query(min_length=2)`, then strip to an empty term that `_like_prefix` turned into
        a bare `%` — matching every non-exited employee in the tenant. Stripping now runs
        *before* the length rule (`StringConstraints(strip_whitespace=True, min_length=2)`), so
        the stripped-to-empty term is refused here, at the boundary, rather than reaching the
        selector as an unbounded scan."""
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(session, org_id, uuid.uuid4(), display_name="Anyone At All")

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegate-candidates", params={"q": "  "})
        assert response.status_code == 422

    async def test_a_single_character_padded_with_whitespace_is_rejected(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`q="a "` also has raw length 2, but strips to a one-character prefix — the exact
        shape ADR-0006 §11 forbids ("at least two characters"), and the second half of the same
        bypass: normalising after the length check let a caller walk `"a ".."z "`, `"0 ".."9 "`
        and enumerate the tenant's staff directory in a few dozen requests."""
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegate-candidates", params={"q": "a "})
        assert response.status_code == 422

    async def test_a_valid_query_padded_with_whitespace_still_matches(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Stripping must narrow the bypass, not the legitimate case: a real prefix padded with
        incidental whitespace (a copy-pasted name, a trailing space from an input field) still
        searches normally."""
        caller, priya = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(session, org_id, priya, display_name="Priya Nair")

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "  pri  "}
            )
        assert response.status_code == 200
        assert {c["display_name"] for c in response.json()} == {"Priya Nair"}

    async def test_q_is_required(self, client, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegate-candidates")
        assert response.status_code == 422


class TestExclusions:
    async def test_the_caller_is_excluded_from_their_own_candidate_list(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller, display_name="Priya Caller")

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "Priya"}
            )
        assert response.status_code == 200
        assert response.json() == []

    async def test_an_exited_employee_is_excluded(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller, exited = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(
                session, org_id, exited, display_name="Departed Employee", status="exited"
            )

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "Departed"}
            )
        assert response.status_code == 200
        assert response.json() == []

    async def test_a_caller_with_no_subject_ref_still_gets_the_full_candidate_set(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """No `subject_ref` means nothing to exclude, not "exclude everyone" — the same
        distinction `IS DISTINCT FROM NULL` (`app/selectors.py`) exists to make."""
        candidate = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, candidate, display_name="Findable Person")

        principal = principal_for(org_id, None, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "Findable"}
            )
        assert response.status_code == 200
        assert {c["employee_id"] for c in response.json()} == {str(candidate)}


class TestAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id: uuid.UUID) -> None:
        response = await client.get("/api/v1/approvals/delegate-candidates", params={"q": "an"})
        assert response.status_code == 401

    async def test_a_caller_with_no_delegation_write_grant_is_refused(
        self, client, org_id: uuid.UUID
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegate-candidates", params={"q": "an"})
        assert response.status_code == 403

    async def test_read_only_does_not_reach_the_picker(self, client, org_id: uuid.UUID) -> None:
        """`delegation.write`, not `request.read` — holding the read permission alone must not
        be enough (ADR-0006 §11: "gated on delegation.write rather than read")."""
        principal = principal_for(org_id, uuid.uuid4(), ["core.approval.request.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegate-candidates", params={"q": "an"})
        assert response.status_code == 403

    async def test_team_scope_reaches_the_picker(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`delegation.write`'s `max_scope` is ORG, so a TEAM grant is legal on the permission
        ladder — this endpoint does not itself branch on scope (unlike list/create/revoke, it
        has no "who else" reading to collapse), but nothing proved a TEAM grant even clears the
        `require(...)` gate rather than being unexpectedly refused."""
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(session, org_id, uuid.uuid4(), display_name="Priya Nair")

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:TEAM"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "pri"}
            )
        assert response.status_code == 200
        assert {c["display_name"] for c in response.json()} == {"Priya Nair"}

    async def test_location_scope_reaches_the_picker(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Same regression, LOCATION."""
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(session, org_id, uuid.uuid4(), display_name="Priya Nair")

        principal = principal_for(
            org_id, caller, [f"{DELEGATION_WRITE}:LOCATION"], locations=["BLR"]
        )
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "pri"}
            )
        assert response.status_code == 200
        assert {c["display_name"] for c in response.json()} == {"Priya Nair"}


class TestCrossTenantIsolation:
    async def test_another_organizations_employee_never_appears(
        self,
        client,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
        async with admin_session.begin():
            await seed_person(
                admin_session, other_org, uuid.uuid4(), display_name="Other Tenant Person"
            )

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegate-candidates", params={"q": "Other"}
            )
        assert response.status_code == 200
        assert response.json() == []
