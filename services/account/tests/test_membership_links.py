"""ADR-0008: `account.memberships.employee_id` becomes writable, by exactly one path.

That column is the sole origin of the `sref` claim every OWN-scoped check in ten services
resolves against, and until this slice **nothing in the platform wrote it** — a real
onboarding produced an approval inbox that was empty at HTTP 200 with no error anywhere.
These tests cover the writer and the two things that make it an authorization change rather
than a data-entry field:

* the **conflict** an employee already linked elsewhere causes is a 409 that names the
  holding membership — from the pre-check *and* from the raced path the pre-check misses,
  and never from an unrelated constraint wearing this one's message;
* the **`pv` asymmetry** ADR-0008 §7 states: a first link does not bump (a grant, worth one
  access-token lifetime), an unlink or a re-link does (a revocation, worth a tenant-wide
  refresh burst). All three cases are proved, because an implementation that bumped on every
  write would pass a test that only checked the revocation half;
* `?linked=false` is a filter **inside** the scope `members_stmt` already applies, at every
  rung of the ladder, with the negative cases that make that claim mean anything.

And the reconcile (`tools/reconcile_membership_links.py`): it fills a null, never overwrites
one, is a true no-op on re-run, rolls back under `--dry-run`, and never bumps `pv`.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import Update, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import AuditLog, Membership, Organization
from app.schemas import MemberPatch, OrganizationIn
from platform_core.auth import Scope
from platform_core.cache import permission_version_key
from platform_core.errors import ConflictProblem
from tests.helpers import FakeRedis, ctx_for, principal_for

pytestmark = pytest.mark.integration

# `tools/` is not a package and sits outside the service directory; reached the same way
# `tests/test_reconcile_permissions.py` reaches its own CLI, because these tests exercise the
# command's real orchestration rather than a reimplementation of it.
_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import reconcile_membership_links  # type: ignore[import-not-found]  # noqa: E402

MEMBER_WRITE = "core.identity.membership.write"
MEMBER_READ = "core.identity.membership.read"


async def _patch(
    session: AsyncSession,
    org: Organization,
    actor: Membership,
    target: Membership,
    **fields: object,
) -> Membership:
    """One `PATCH /members/{id}` through the service layer, in its own transaction."""
    ctx = ctx_for(org.id, actor, MEMBER_WRITE, Scope.ORG)
    async with session.begin():
        return await services.update_member(session, ctx, target, MemberPatch(**fields))


async def _employee_id_of(session: AsyncSession, membership_id: uuid.UUID) -> uuid.UUID | None:
    """Read the column back from the database, not from the identity map."""
    async with session.begin():
        return (
            await session.execute(
                select(Membership.employee_id).where(Membership.id == membership_id)
            )
        ).scalar_one()


class TestLinkingAnEmployee:
    async def test_a_first_link_is_written_and_read_back(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        employee_id = uuid.uuid4()
        await _patch(
            session, org, people["manager"], people["report"], employee_id=str(employee_id)
        )
        assert await _employee_id_of(session, people["report"].id) == employee_id

    async def test_an_absent_field_leaves_an_existing_link_alone(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """`exclude_unset` carries the distinction this field needs: absent is not null."""
        employee_id = uuid.uuid4()
        await _patch(
            session, org, people["manager"], people["report"], employee_id=str(employee_id)
        )
        await _patch(session, org, people["manager"], people["report"], job_title="Engineer")
        assert await _employee_id_of(session, people["report"].id) == employee_id

    async def test_an_explicit_null_unlinks(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        await _patch(session, org, people["manager"], people["report"], employee_id=uuid.uuid4())
        await _patch(session, org, people["manager"], people["report"], employee_id=None)
        assert await _employee_id_of(session, people["report"].id) is None

    async def test_the_link_is_in_the_audit_trail_on_both_sides(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """ADR-0008 §10: an identity reassignment that leaves no trail is not defensible."""
        first, second = uuid.uuid4(), uuid.uuid4()
        await _patch(session, org, people["manager"], people["report"], employee_id=first)
        await _patch(session, org, people["manager"], people["report"], employee_id=second)

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(AuditLog)
                        .where(
                            AuditLog.entity_id == people["report"].id,
                            AuditLog.action == "membership.updated",
                        )
                        .order_by(AuditLog.at)
                    )
                )
                .scalars()
                .all()
            )
        assert [(r.before["employee_id"], r.after["employee_id"]) for r in rows] == [
            ("", str(first)),
            (str(first), str(second)),
        ]

    async def test_the_link_travels_with_the_rest_of_the_patch(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Handled explicitly *and* still part of one PATCH: the field is popped out of
        `changes` before the blind `setattr` loop (ADR-0008 §2), which must not make it a
        second request or lose the other fields."""
        employee_id = uuid.uuid4()
        updated = await _patch(
            session,
            org,
            people["manager"],
            people["report"],
            employee_id=employee_id,
            job_title="Staff Engineer",
        )
        assert updated.job_title == "Staff Engineer"
        assert await _employee_id_of(session, people["report"].id) == employee_id

    async def test_the_event_names_the_link_among_the_changed_fields(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        from app.models import OutboxEvent

        await _patch(
            session,
            org,
            people["manager"],
            people["report"],
            employee_id=uuid.uuid4(),
            job_title="Staff Engineer",
        )
        async with session.begin():
            event = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == services.EVT_MEMBERSHIP_UPDATED
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert event.payload["changed"] == ["employee_id", "job_title"]


class TestConflictOnTheUniqueIndex:
    async def test_a_second_membership_claiming_a_linked_employee_is_refused(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """A 409 naming the holder — ADR-0008 §5. Never a raw 23505."""
        employee_id = uuid.uuid4()
        # Read the ids up front: the refused PATCH rolls this shared session's transaction
        # back and expires every loaded object, so a later attribute access would try to
        # refresh outside the async context. A real client gets a session per request.
        holder_id = people["report"].id
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)

        with pytest.raises(ConflictProblem) as excinfo:
            await _patch(
                session, org, people["manager"], people["outsider"], employee_id=employee_id
            )

        problem = excinfo.value
        assert problem.status_code == 409
        assert "already linked to another member" in problem.detail
        assert problem.errors is not None
        assert problem.errors[0]["membership_id"] == str(holder_id)
        assert problem.errors[0]["code"] == "employee_already_linked"

    async def test_nothing_is_ever_auto_unlinked(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The refused PATCH leaves the holder exactly as it was (ADR-0008 §5)."""
        employee_id = uuid.uuid4()
        holder_id, claimant_id = people["report"].id, people["outsider"].id
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)
        with pytest.raises(ConflictProblem):
            await _patch(
                session, org, people["manager"], people["outsider"], employee_id=employee_id
            )

        assert await _employee_id_of(session, holder_id) == employee_id
        assert await _employee_id_of(session, claimant_id) is None

    async def test_relinking_the_same_employee_to_the_same_membership_is_not_a_conflict(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Idempotent: writing the same value twice is a no-op, not a self-conflict."""
        employee_id = uuid.uuid4()
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)
        assert await _employee_id_of(session, people["report"].id) == employee_id

    async def test_a_revoked_membership_still_holds_its_link_and_still_conflicts(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """ADR-0008 §5's documented rehire edge, asserted rather than assumed.

        `revoke_member` keeps the row and the link, and the partial unique index does not
        exclude removed memberships — so somebody returning under a different email hits a
        409 naming the *old* membership. That is the correct outcome, and the message naming
        the holder is what makes it a thirty-second fix.
        """
        employee_id = uuid.uuid4()
        holder_id = people["report"].id
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)

        ctx = ctx_for(org.id, people["manager"], MEMBER_WRITE, Scope.ORG)
        async with session.begin():
            await services.revoke_member(session, ctx, people["report"])

        with pytest.raises(ConflictProblem) as excinfo:
            await _patch(
                session, org, people["manager"], people["outsider"], employee_id=employee_id
            )
        assert excinfo.value.errors is not None
        assert excinfo.value.errors[0]["membership_id"] == str(holder_id)


class TestTheRaceThePrecheckMisses:
    """The pre-check is what makes the common case legible; the `except IntegrityError` is
    what makes it correct. Every conflict test above exits through the pre-check and never
    reaches the write with a live conflict, so the raced path would otherwise be unproven.
    """

    async def test_a_race_the_precheck_misses_still_gets_a_clean_409_naming_the_holder(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Defeats the pre-check — returning `None` exactly as a transaction that commits
        between our read and our write would — so `uq_memberships_org_employee` itself is
        what refuses the second link, and the `except IntegrityError` branch is what turns it
        into a 409. The holder is still named, which is the tell that the *write* path and not
        the pre-check produced this error: the UPDATE runs inside a savepoint, so the
        transaction survives the violation and can still answer one more question.
        """
        employee_id = uuid.uuid4()
        holder_id = people["report"].id
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)

        calls: list[str] = []
        real = selectors.get_membership_by_employee

        async def _miss_once(*args: object, **kwargs: object) -> Membership | None:
            calls.append("x")
            if len(calls) == 1:  # the pre-check: pretend the holder is not there yet
                return None
            return await real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(services.selectors, "get_membership_by_employee", _miss_once)

        with pytest.raises(ConflictProblem) as excinfo:
            await _patch(
                session, org, people["manager"], people["outsider"], employee_id=employee_id
            )

        assert len(calls) == 2, "the pre-check missed and the catch re-read — both ran"
        assert excinfo.value.errors is not None
        assert excinfo.value.errors[0]["membership_id"] == str(holder_id)

    async def test_an_integrity_error_from_another_constraint_is_not_mislabelled(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An `IntegrityError` whose constraint is not `uq_memberships_org_employee` must
        propagate as-is — `platform_core.errors`' handler turns it into a generic, non-leaking
        409 — never as the link-specific message. A broad `except IntegrityError` around the
        write would report an unrelated violation as "that employee is already linked", which
        for anything else is simply false. That is the bug the delegation slice shipped, and
        this is its regression guard on this surface.
        """

        class _FakeDriverError(Exception):
            constraint_name = "uq_memberships_user_org"

        real_execute = session.execute

        async def _fake_execute(statement: object, *args: object, **kwargs: object) -> object:
            if isinstance(statement, Update):
                raise IntegrityError("UPDATE", {}, _FakeDriverError())
            return await real_execute(statement, *args, **kwargs)  # type: ignore[arg-type]

        ctx = ctx_for(org.id, people["manager"], MEMBER_WRITE, Scope.ORG)
        with pytest.raises(IntegrityError):
            async with session.begin():
                monkeypatch.setattr(session, "execute", _fake_execute)
                await services.update_member(
                    session,
                    ctx,
                    people["report"],
                    MemberPatch(employee_id=uuid.uuid4()),
                )


class TestPermissionVersionAsymmetry:
    """ADR-0008 §7. `pv` is per **organization**, so every bump is a tenant-wide token-refresh
    burst: bumping on a first link would fire one several times a day in a 4,000-person
    customer, and *not* bumping on an unlink would leave a corrected impersonation live for
    the remaining 900 seconds of the wrong token.
    """

    async def test_a_first_link_does_not_bump(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        key = permission_version_key(str(org.id))
        await _patch(session, org, people["manager"], people["report"], employee_id=uuid.uuid4())
        assert key not in redis.store, "onboarding is a grant — it waits out the access token"

    async def test_an_unlink_bumps(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        key = permission_version_key(str(org.id))
        await _patch(session, org, people["manager"], people["report"], employee_id=uuid.uuid4())
        await _patch(session, org, people["manager"], people["report"], employee_id=None)
        assert redis.store.get(key) == "1", "a revocation must not wait out the token"

    async def test_a_relink_bumps(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        """Correcting a wrong link is the case this exists for: the old identity must die at
        the gateway now, not in fifteen minutes."""
        key = permission_version_key(str(org.id))
        await _patch(session, org, people["manager"], people["report"], employee_id=uuid.uuid4())
        assert key not in redis.store
        await _patch(session, org, people["manager"], people["report"], employee_id=uuid.uuid4())
        assert redis.store.get(key) == "1"

    async def test_writing_the_same_link_again_bumps_nothing(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        employee_id = uuid.uuid4()
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)
        assert permission_version_key(str(org.id)) not in redis.store


class TestLinkedFilterAndsWithScope:
    """`?linked=false` is an authorization surface, not a convenience. It must narrow within
    the scope `members_stmt` already applies and never escape it — the same property
    `test_authz.py::test_a_search_cannot_widen_scope` pins for `q`.
    """

    async def _emails(self, session: AsyncSession, ctx: object, **kwargs: object) -> set[str]:
        stmt = selectors.members_stmt(ctx, **kwargs)  # type: ignore[arg-type]
        rows = (await session.execute(stmt)).all()
        return {r[1].email for r in rows}

    @pytest.fixture(autouse=True)
    async def _link_one_person(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The report is linked; the other three are not."""
        await _patch(session, org, people["manager"], people["report"], employee_id=uuid.uuid4())

    async def test_org_scope_sees_every_unlinked_person_and_no_linked_one(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.ORG)
        assert await self._emails(session, ctx, linked=False) == {
            "manager@example.com",
            "other@example.com",
            "outsider@example.com",
        }
        assert await self._emails(session, ctx, linked=True) == {"report@example.com"}

    async def test_team_scope_cannot_reach_an_unlinked_outsider(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The negative one: the outsider *is* unlinked, so a filter that replaced the scope
        predicate instead of ANDing onto it would hand them over."""
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.TEAM)
        assert await self._emails(session, ctx, linked=False) == set()
        assert await self._emails(session, ctx, linked=True) == {"report@example.com"}

    async def test_own_scope_sees_only_itself(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org.id, people["outsider"], MEMBER_READ, Scope.OWN)
        assert await self._emails(session, ctx, linked=False) == {"outsider@example.com"}

    async def test_location_scope_is_still_confined_to_its_sites(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.LOCATION, locations=["DEL"])
        assert await self._emails(session, ctx, linked=False) == {"outsider@example.com"}

    async def test_location_scope_with_no_locations_still_sees_nothing(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The failure that matters: an empty location list means none, never all — and a
        `linked=false` filter must not turn "no rows" into "every unlinked row"."""
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.LOCATION, locations=[])
        assert await self._emails(session, ctx, linked=False) == set()

    async def test_the_endpoint_refuses_a_caller_without_the_read_permission(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Negative authz on the HTTP surface: the filter is not a way around the guard."""
        from app.main import app
        from platform_core.auth import get_principal

        principal = principal_for(org.id, people["report"], ["core.org.organization.read:ORG"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get("/api/v1/members", params={"linked": "false"})
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 403

    async def test_the_endpoint_applies_the_callers_scope_not_the_organization(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        """A TEAM-scoped holder asking for unlinked members gets their team's, not the
        tenant's — through the route, so the wiring is proved and not just the selector."""
        from app.main import app
        from platform_core.auth import get_principal

        principal = principal_for(org.id, people["manager"], [f"{MEMBER_READ}:TEAM"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            unlinked = await client.get("/api/v1/members", params={"linked": "false"})
            linked = await client.get("/api/v1/members", params={"linked": "true"})
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert unlinked.status_code == 200
        assert unlinked.json()["items"] == []
        assert [i["email"] for i in linked.json()["items"]] == ["report@example.com"]


class TestNobodyAssignsTheirOwnIdentity:
    """`employee_id` is the sole origin of `sref`. An administrator who could PATCH it on their
    own membership could become any employee — OWN scope over that person's records, TEAM scope
    over their reports, approval decisions credited to them (ADR-0008: a wrong link is an
    authorization defect). It is refused in **every** direction — link, re-link, unlink — for the
    caller's own membership, and only for that: the second administrator is the control."""

    URL = "/api/v1/members/{}"

    async def _patch_over_http(
        self,
        client: AsyncClient,
        principal: object,
        membership_id: uuid.UUID,
        body: dict[str, object],
    ):
        from app.main import app
        from platform_core.auth import get_principal

        app.dependency_overrides[get_principal] = lambda: principal
        try:
            return await client.patch(self.URL.format(membership_id), json=body)
        finally:
            app.dependency_overrides.pop(get_principal, None)

    async def _events_and_audits(self, session: AsyncSession) -> tuple[int, int]:
        from sqlalchemy import func

        from app.models import OutboxEvent

        async with session.begin():
            audits = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "membership.updated")
                )
            ).scalar_one()
            events = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == services.EVT_MEMBERSHIP_UPDATED)
                )
            ).scalar_one()
        return audits, events

    async def test_an_administrator_cannot_link_their_own_membership_to_an_employee(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        ids = {name: member.id for name, member in people.items()}
        victim = uuid.uuid4()
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])

        response = await self._patch_over_http(
            client, principal, ids["manager"], {"employee_id": str(victim)}
        )

        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        problem = response.json()
        assert "your own employee link" in problem["detail"]
        assert problem["errors"][0]["code"] == "cannot_change_own_employee_link"
        assert problem["errors"][0]["field"] == "employee_id"
        assert await _employee_id_of(session, ids["manager"]) is None

    async def test_the_refusal_is_the_same_whether_or_not_somebody_holds_the_employee(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        """A 409 here would say who holds the employee; the refusal must come first and say
        nothing about it."""
        ids = {name: member.id for name, member in people.items()}
        held = uuid.uuid4()
        await _patch(session, org, people["manager"], people["report"], employee_id=held)
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])

        response = await self._patch_over_http(
            client, principal, ids["manager"], {"employee_id": str(held)}
        )

        assert response.status_code == 403
        assert str(ids["report"]) not in response.text

    async def test_it_is_refused_when_the_target_is_the_callers_own_employee_record(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The token's `sref` is the employee the caller already *is*. Linking their own membership
        to that record is still a change of identity, made by the person it changes."""
        ids = {name: member.id for name, member in people.items()}
        from platform_core.auth import Principal, ScopeContext

        mine = uuid.uuid4()
        principal = Principal(
            sub=people["manager"].user_id,
            organization_id=org.id,
            membership_id=ids["manager"],
            subject_ref=mine,
            perms=[f"{MEMBER_WRITE}:ORG"],
        )
        ctx = ScopeContext(principal=principal, scope=Scope.ORG, permission=MEMBER_WRITE)

        from platform_core.errors import ForbiddenProblem

        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.update_member(
                    session, ctx, people["manager"], MemberPatch(employee_id=mine)
                )
        assert await _employee_id_of(session, ids["manager"]) is None

    async def test_an_administrator_cannot_unlink_themselves(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        """Unlinking is a change of identity too, and the one that bumps the tenant's
        permission version — not something a caller does to their own token."""
        ids = {name: member.id for name, member in people.items()}
        mine = uuid.uuid4()
        await _patch(session, org, people["other_manager"], people["manager"], employee_id=mine)
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])

        response = await self._patch_over_http(
            client, principal, ids["manager"], {"employee_id": None}
        )

        assert response.status_code == 403
        assert await _employee_id_of(session, ids["manager"]) == mine

    async def test_an_administrator_cannot_swap_their_own_link(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        ids = {name: member.id for name, member in people.items()}
        mine = uuid.uuid4()
        await _patch(session, org, people["other_manager"], people["manager"], employee_id=mine)
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])

        response = await self._patch_over_http(
            client, principal, ids["manager"], {"employee_id": str(uuid.uuid4())}
        )

        assert response.status_code == 403
        assert await _employee_id_of(session, ids["manager"]) == mine

    async def test_a_token_with_no_membership_claim_is_still_recognised_as_the_same_person(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """A user has one membership per organization, so `sub` identifies the caller even when the
        token carries no `mem`."""
        ids = {name: member.id for name, member in people.items()}
        from platform_core.auth import Principal, ScopeContext
        from platform_core.errors import ForbiddenProblem

        principal = Principal(
            sub=people["manager"].user_id,
            organization_id=org.id,
            membership_id=None,
            perms=[f"{MEMBER_WRITE}:ORG"],
        )
        ctx = ScopeContext(principal=principal, scope=Scope.ORG, permission=MEMBER_WRITE)
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.update_member(
                    session, ctx, people["manager"], MemberPatch(employee_id=uuid.uuid4())
                )
        assert await _employee_id_of(session, ids["manager"]) is None

    async def test_a_refused_attempt_writes_no_audit_row_and_no_event(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        ids = {name: member.id for name, member in people.items()}
        before = await self._events_and_audits(session)
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])
        response = await self._patch_over_http(
            client, principal, ids["manager"], {"employee_id": str(uuid.uuid4())}
        )
        assert response.status_code == 403
        assert await self._events_and_audits(session) == before

    async def test_a_second_administrator_can_still_link_and_unlink_the_first(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        """The control is a second pair of eyes, not a ban: another member with the permission
        makes exactly the change the first was refused."""
        ids = {name: member.id for name, member in people.items()}
        target_employee = uuid.uuid4()
        other_admin = principal_for(org.id, people["other_manager"], [f"{MEMBER_WRITE}:ORG"])

        linked = await self._patch_over_http(
            client, other_admin, ids["manager"], {"employee_id": str(target_employee)}
        )
        assert linked.status_code == 200
        assert linked.json()["employee_id"] == str(target_employee)
        assert await _employee_id_of(session, ids["manager"]) == target_employee

        unlinked = await self._patch_over_http(
            client, other_admin, ids["manager"], {"employee_id": None}
        )
        assert unlinked.status_code == 200
        assert await _employee_id_of(session, ids["manager"]) is None

    async def test_linking_somebody_else_is_unchanged(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        ids = {name: member.id for name, member in people.items()}
        employee_id = uuid.uuid4()
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])
        response = await self._patch_over_http(
            client, principal, ids["report"], {"employee_id": str(employee_id)}
        )
        assert response.status_code == 200
        assert await _employee_id_of(session, ids["report"]) == employee_id

    async def test_an_administrator_may_still_edit_everything_else_on_their_own_membership(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        ids = {name: member.id for name, member in people.items()}
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])
        response = await self._patch_over_http(
            client, principal, ids["manager"], {"job_title": "Head of People"}
        )
        assert response.status_code == 200
        assert response.json()["job_title"] == "Head of People"

    async def test_re_sending_the_link_one_already_holds_changes_no_identity(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        ids = {name: member.id for name, member in people.items()}
        mine = uuid.uuid4()
        await _patch(session, org, people["other_manager"], people["manager"], employee_id=mine)
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])
        response = await self._patch_over_http(
            client,
            principal,
            ids["manager"],
            {"employee_id": str(mine), "job_title": "Head of People"},
        )
        assert response.status_code == 200
        assert await _employee_id_of(session, ids["manager"]) == mine

    async def test_a_membership_in_another_tenant_is_still_a_404(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        """Unchanged, and ahead of the self check: another tenant's membership id says nothing."""
        async with admin_session.begin():
            other_org = await services.create_organization(
                admin_session, OrganizationIn(name="Other Co", slug="other-co"), actor_user_id=None
            )
            theirs = await services.add_member(
                admin_session,
                org_id=other_org.id,
                email="theirs@other.example",
                full_name="Thea Theirs",
                actor_user_id=None,
                role_codes=["MEMBER"],
            )
        principal = principal_for(org.id, people["manager"], [f"{MEMBER_WRITE}:ORG"])
        response = await self._patch_over_http(
            client, principal, theirs.id, {"employee_id": str(uuid.uuid4())}
        )
        assert response.status_code == 404

    async def test_a_caller_without_the_permission_is_refused_before_any_of_this(
        self,
        org: Organization,
        people: dict[str, Membership],
        client: AsyncClient,
    ) -> None:
        ids = {name: member.id for name, member in people.items()}
        principal = principal_for(org.id, people["report"], ["core.identity.membership.read:ORG"])
        response = await self._patch_over_http(
            client, principal, ids["report"], {"employee_id": str(uuid.uuid4())}
        )
        assert response.status_code == 403
        assert "Missing permission" in response.json()["detail"]


class TestEmployeeIdFilter:
    """`?employee_id=` answers "which member is this employee?" — what the HRMS employee page
    asks to show its "Linked member" field (ADR-0008 Q1, NEW-5). It is an authorization surface
    like `?linked=`: ANDed onto the scope `members_stmt` applies, never in place of it, and
    bounded by the tenant."""

    async def _emails(self, session: AsyncSession, ctx: object, **kwargs: object) -> set[str]:
        stmt = selectors.members_stmt(ctx, **kwargs)  # type: ignore[arg-type]
        rows = (await session.execute(stmt)).all()
        return {r[1].email for r in rows}

    @pytest.fixture
    async def employee_id(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> uuid.UUID:
        """The report is linked to this employee; nobody else is."""
        linked_to = uuid.uuid4()
        await _patch(session, org, people["manager"], people["report"], employee_id=linked_to)
        return linked_to

    async def test_it_returns_the_one_member_linked_to_that_employee(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        employee_id: uuid.UUID,
    ) -> None:
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.ORG)
        assert await self._emails(session, ctx, employee_id=employee_id) == {"report@example.com"}

    async def test_an_employee_nobody_is_linked_to_matches_nobody(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        employee_id: uuid.UUID,
    ) -> None:
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.ORG)
        assert await self._emails(session, ctx, employee_id=uuid.uuid4()) == set()

    async def test_the_filter_never_matches_the_unlinked_by_being_absent(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        employee_id: uuid.UUID,
    ) -> None:
        """An omitted filter lists everyone; a supplied one lists only its match. There is no
        value that turns "unlinked" (a null column) into a hit."""
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.ORG)
        assert len(await self._emails(session, ctx)) == 4
        assert await self._emails(session, ctx, employee_id=employee_id) == {"report@example.com"}

    async def test_team_scope_cannot_reach_a_member_outside_the_team_by_naming_the_employee(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The outsider is linked to an employee and the manager names that employee: the
        filter matches, the scope does not, and nothing comes back."""
        outsider_employee = uuid.uuid4()
        await _patch(
            session, org, people["other_manager"], people["outsider"], employee_id=outsider_employee
        )
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.TEAM)
        assert await self._emails(session, ctx, employee_id=outsider_employee) == set()

    async def test_own_scope_sees_only_itself(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        employee_id: uuid.UUID,
    ) -> None:
        ctx = ctx_for(org.id, people["outsider"], MEMBER_READ, Scope.OWN)
        assert await self._emails(session, ctx, employee_id=employee_id) == set()

    async def test_location_scope_with_no_locations_still_sees_nothing(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        employee_id: uuid.UUID,
    ) -> None:
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.LOCATION, locations=[])
        assert await self._emails(session, ctx, employee_id=employee_id) == set()

    async def test_another_tenants_member_linked_to_the_same_employee_id_is_not_returned(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        employee_id: uuid.UUID,
    ) -> None:
        """Two organizations may each link a member to an employee record with the same id (an
        id is only unique within its tenant's own HR data as far as account can tell). Naming it
        returns this tenant's member and only this tenant's."""
        async with admin_session.begin():
            other_org = await services.create_organization(
                admin_session, OrganizationIn(name="Other Co", slug="other-co"), actor_user_id=None
            )
            theirs = await services.add_member(
                admin_session,
                org_id=other_org.id,
                email="theirs@other.example",
                full_name="Thea Theirs",
                actor_user_id=None,
                role_codes=["MEMBER"],
            )
            theirs.employee_id = employee_id

        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.ORG)
        assert await self._emails(session, ctx, employee_id=employee_id) == {"report@example.com"}

    async def test_the_endpoint_filters_by_employee_within_the_callers_scope(
        self,
        client: AsyncClient,
        org: Organization,
        people: dict[str, Membership],
        employee_id: uuid.UUID,
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        principal = principal_for(org.id, people["manager"], [f"{MEMBER_READ}:ORG"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            hit = await client.get("/api/v1/members", params={"employee_id": str(employee_id)})
            miss = await client.get("/api/v1/members", params={"employee_id": str(uuid.uuid4())})
            bad = await client.get("/api/v1/members", params={"employee_id": "not-a-uuid"})
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert hit.status_code == 200
        assert [i["email"] for i in hit.json()["items"]] == ["report@example.com"]
        assert hit.json()["items"][0]["employee_id"] == str(employee_id)
        assert miss.json()["items"] == []
        assert bad.status_code == 422

    async def test_the_endpoint_refuses_a_caller_without_the_read_permission(
        self,
        client: AsyncClient,
        org: Organization,
        people: dict[str, Membership],
        employee_id: uuid.UUID,
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        principal = principal_for(org.id, people["report"], ["core.org.organization.read:ORG"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get("/api/v1/members", params={"employee_id": str(employee_id)})
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 403


# --------------------------------------------------------------------- the reconcile


class _StubLinkSource:
    """Employee-service's `GET /internal/v1/employee-links`, one page.

    Returns **every** employee with its `membership_id` — null or not — because that is the
    contract, and because the verify half is meaningless without the unlinked rows: an
    employee that exists but is not yet pointed at a membership would otherwise be reported
    as an id employee does not recognise.
    """

    def __init__(self, rows: list[tuple[uuid.UUID, uuid.UUID | None]]) -> None:
        self.rows = rows
        self.calls = 0

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = 500
    ) -> object:
        self.calls += 1
        return reconcile_membership_links.LinkPage(
            items=[
                reconcile_membership_links.LinkRow(employee_id=e, membership_id=m)
                for e, m in self.rows
            ],
            next_cursor=None,
        )


class TestReconcileFillsAndVerifies:
    async def test_it_fills_a_null(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        employee_id = uuid.uuid4()
        links = [services.EmployeeLink(employee_id=employee_id, membership_id=people["report"].id)]

        async with session.begin():
            summary = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )

        assert summary.links_written == 1
        assert summary.changed is True
        assert await _employee_id_of(session, people["report"].id) == employee_id

    async def test_it_never_overwrites_a_populated_value(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The rule that makes this safe beside live traffic: the interactive path always
        wins, so no version gate is needed (ADR-0008 §6)."""
        interactive = uuid.uuid4()
        await _patch(session, org, people["manager"], people["report"], employee_id=interactive)

        links = [services.EmployeeLink(employee_id=uuid.uuid4(), membership_id=people["report"].id)]
        async with session.begin():
            summary = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )

        assert summary.links_written == 0
        assert summary.conflicts_disagree == 1
        assert await _employee_id_of(session, people["report"].id) == interactive

    async def test_an_employee_already_held_elsewhere_is_reported_not_moved(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        employee_id = uuid.uuid4()
        await _patch(session, org, people["manager"], people["report"], employee_id=employee_id)

        links = [
            services.EmployeeLink(employee_id=employee_id, membership_id=people["outsider"].id)
        ]
        async with session.begin():
            summary = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )

        assert summary.links_written == 0
        assert summary.conflicts_employee_taken == 1
        assert summary.conflicts[0].held_by_membership_id == people["report"].id
        assert await _employee_id_of(session, people["outsider"].id) is None

    async def test_the_verify_half_finds_an_id_employee_does_not_recognise(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """ADR-0008 §4's mistyped UUID — accepted by the write, found only here."""
        typo = uuid.uuid4()
        real = uuid.uuid4()
        await _patch(session, org, people["manager"], people["report"], employee_id=typo)

        # The page carries a real employee that nobody has linked yet. It must not be
        # mistaken for a conflict, and the typo must not be hidden by it.
        links = [services.EmployeeLink(employee_id=real, membership_id=None)]
        async with session.begin():
            summary = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )

        assert summary.unrecognised_employee_ids == [typo]
        assert summary.links_written == 0
        assert summary.changed is False

    async def test_an_unlinked_employee_in_the_page_is_not_a_finding(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """A tenant mid-rollout is mostly unlinked employees. They are the normal case."""
        links = [
            services.EmployeeLink(employee_id=uuid.uuid4(), membership_id=None) for _ in range(3)
        ]
        async with session.begin():
            summary = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )
        assert summary.links_written == 0
        assert summary.unrecognised_employee_ids == []
        assert summary.conflicts == []
        assert summary.memberships_unlinked == 4

    async def test_a_membership_id_account_does_not_have_is_reported(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        stranger = uuid.uuid4()
        links = [services.EmployeeLink(employee_id=uuid.uuid4(), membership_id=stranger)]
        async with session.begin():
            summary = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )
        assert summary.unrecognised_membership_ids == [stranger]
        assert summary.links_written == 0

    async def test_a_second_run_is_a_true_no_op(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        employee_id = uuid.uuid4()
        links = [services.EmployeeLink(employee_id=employee_id, membership_id=people["report"].id)]

        async with session.begin():
            first = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )
        async with session.begin():
            second = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )

        assert first.links_written == 1
        assert second.links_written == 0
        assert second.changed is False
        assert second.conflicts == []

        async with session.begin():
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "membership.links_reconciled")
                    )
                )
                .scalars()
                .all()
            )
        assert len(audits) == 1, "an empty diff writes no audit row"
        assert audits[0].acting_as == "test"
        assert audits[0].actor_id is None
        assert audits[0].after["links_written"] == 1

    async def test_it_never_bumps_the_permission_version(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        """ADR-0008 §8: every write it makes is grant-shaped, and a fleet run must not be a
        fleet-wide token-refresh storm."""
        links = [
            services.EmployeeLink(employee_id=uuid.uuid4(), membership_id=people["report"].id),
            services.EmployeeLink(employee_id=uuid.uuid4(), membership_id=people["outsider"].id),
        ]
        async with session.begin():
            summary = await services.reconcile_employee_links(
                session, org.id, links, acting_as="test"
            )
        assert summary.links_written == 2
        assert permission_version_key(str(org.id)) not in redis.store

    async def test_memberships_unlinked_counts_only_active_members(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org.id, people["manager"], MEMBER_WRITE, Scope.ORG)
        async with session.begin():
            await services.revoke_member(session, ctx, people["outsider"])

        async with session.begin():
            summary = await services.reconcile_employee_links(session, org.id, [], acting_as="t")
        assert summary.memberships_unlinked == 3


class TestReconcileCommand:
    async def test_dry_run_reports_the_fill_and_rolls_it_back(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        employee_id = uuid.uuid4()
        source = _StubLinkSource([(employee_id, people["report"].id)])

        line = await reconcile_membership_links.reconcile_organization(
            org.id, source, dry_run=True, page_pace_ms=0
        )

        assert line["links_written"] == 1
        assert line["dry_run"] is True
        assert await _employee_id_of(session, people["report"].id) is None, (
            "--dry-run must always roll back, including when it found something"
        )

    async def test_a_real_run_commits_through_the_app_role(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        employee_id = uuid.uuid4()
        source = _StubLinkSource([(employee_id, people["report"].id), (uuid.uuid4(), None)])

        line = await reconcile_membership_links.reconcile_organization(
            org.id, source, dry_run=False, page_pace_ms=0
        )

        assert line["links_written"] == 1
        assert line["memberships_unlinked"] == 3
        assert line["unrecognised_employee_ids"] == []
        assert await _employee_id_of(session, people["report"].id) == employee_id

    async def test_the_fleet_summary_calls_a_non_zero_result_a_finding(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Expected on this repository is zero. A non-zero run is a finding, and the output
        says so rather than looking like a successful no-op (ADR-0008 §8)."""
        clean = reconcile_membership_links._fleet_summary(
            [
                {
                    "links_written": 0,
                    "conflicts_employee_taken": 0,
                    "conflicts_disagree": 0,
                    "unrecognised_employee_ids": [],
                    "unrecognised_membership_ids": [],
                    "memberships_unlinked": 4,
                }
            ],
            dry_run=True,
        )
        assert clean["finding"] is None
        assert clean["memberships_unlinked_total"] == 4

        dirty = reconcile_membership_links._fleet_summary(
            [
                {
                    "links_written": 2,
                    "conflicts_employee_taken": 0,
                    "conflicts_disagree": 0,
                    "unrecognised_employee_ids": [],
                    "unrecognised_membership_ids": [],
                    "memberships_unlinked": 2,
                }
            ],
            dry_run=False,
        )
        assert dirty["finding"] is not None
        assert "outside this repository" in dirty["finding"]

    async def test_the_fleet_run_is_sequential_and_stops_at_the_first_failure(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        """One transaction per organization: a failure partway leaves what committed alone
        and never attempts what comes after."""
        second, third = uuid.uuid4(), uuid.uuid4()

        seen: list[uuid.UUID] = []

        class _Boom(_StubLinkSource):
            async def fetch_page(self, organization_id, *, cursor=None, limit=500):  # type: ignore[no-untyped-def]
                seen.append(organization_id)
                if organization_id == second:
                    raise reconcile_membership_links.RetryExhaustedError("employee is down")
                return await super().fetch_page(organization_id, cursor=cursor, limit=limit)

        source = _Boom([(uuid.uuid4(), people["report"].id)])

        with pytest.raises(reconcile_membership_links.RetryExhaustedError):
            await reconcile_membership_links.reconcile_fleet(
                [org.id, second, third],
                source,
                dry_run=False,
                pace_ms=0,
                page_pace_ms=0,
            )
        assert seen == [org.id, second], "the third organization was never attempted"
