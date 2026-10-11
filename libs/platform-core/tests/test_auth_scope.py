"""Permission resolution and scope enforcement.

These are the tests that stand between a manager and someone else's team. Two of them
encode defects found in the system this platform replaces, and exist so those defects
cannot come back:

* ``test_location_scope_compares_locations`` — the old ``assert_can_touch`` returned early
  for LOCATION, so a location-scoped caller could reach any object it could name.
* ``test_read_does_not_imply_read_sensitive`` — masked and unmasked PII must never
  collapse into one grant.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Column, String, column, select

from platform_core import OwnerSubject
from platform_core.auth import Principal, Scope, ScopeContext, require_operator
from platform_core.errors import ForbiddenProblem, NotFoundProblem

ORG = uuid.uuid4()
ME = uuid.uuid4()
COLLEAGUE = uuid.uuid4()


def principal(perms: list[str], *, subject: uuid.UUID | None = ME, **kw: Any) -> Principal:
    return Principal(
        sub=uuid.uuid4(),
        organization_id=ORG,
        subject_ref=subject,
        perms=perms,
        **kw,
    )


def ctx(perms: list[str], permission: str, **kw: Any) -> ScopeContext:
    p = principal(perms, **kw)
    scope = p.scope_for(permission)
    assert scope is not None, "fixture asked for a permission the principal does not hold"
    return ScopeContext(principal=p, scope=scope, permission=permission)


class TestScopeResolution:
    def test_exact_match_returns_its_scope(self) -> None:
        p = principal(["crm.pipeline.deal.read:TEAM"])
        assert p.scope_for("crm.pipeline.deal.read") is Scope.TEAM

    def test_missing_permission_is_none(self) -> None:
        p = principal(["crm.pipeline.deal.read:TEAM"])
        assert p.scope_for("crm.pipeline.deal.write") is None
        assert p.has("crm.pipeline.deal.write") is False

    def test_widest_scope_wins_regardless_of_order(self) -> None:
        wide_first = principal(["hr.profile.employee.read:ORG", "hr.profile.employee.read:OWN"])
        narrow_first = principal(["hr.profile.employee.read:OWN", "hr.profile.employee.read:ORG"])
        assert wide_first.scope_for("hr.profile.employee.read") is Scope.ORG
        assert narrow_first.scope_for("hr.profile.employee.read") is Scope.ORG

    def test_a_grant_without_a_scope_is_the_narrowest_reading(self) -> None:
        # Defaulting an unqualified grant to ORG would silently widen every legacy row.
        p = principal(["crm.pipeline.deal.read"])
        assert p.scope_for("crm.pipeline.deal.read") is Scope.OWN

    def test_wildcard_covers_its_own_subtree_only(self) -> None:
        p = principal(["crm.*:ORG"])
        assert p.scope_for("crm.pipeline.deal.read") is Scope.ORG
        assert p.scope_for("hrms.profile.employee.read") is None

    def test_read_does_not_imply_read_sensitive(self) -> None:
        # The wildcard strip keeps the dot, so a prefix is not a match. Masked and
        # unmasked PII are different permissions on purpose.
        p = principal(["hr.profile.employee.read:ORG"])
        assert p.scope_for("hr.profile.employee.read_sensitive") is None


class TestEntitlement:
    def test_product_entitlement_is_separate_from_permission(self) -> None:
        p = principal(["crm.pipeline.deal.read:ORG"], entitlements=["hrms"])
        assert p.has("crm.pipeline.deal.read") is True
        assert p.has_product("crm") is False


class TestObjectLevelChecks:
    def test_org_scope_may_touch_anything_in_the_org(self) -> None:
        ctx(["x.y.z.read:ORG"], "x.y.z.read").assert_can_touch(owner_id=COLLEAGUE)

    def test_own_scope_is_self_only(self) -> None:
        c = ctx(["x.y.z.read:OWN"], "x.y.z.read")
        c.assert_can_touch(owner_id=ME)
        with pytest.raises(ForbiddenProblem):
            c.assert_can_touch(owner_id=COLLEAGUE)

    def test_team_scope_is_direct_reports_only(self) -> None:
        c = ctx(["x.y.z.read:TEAM"], "x.y.z.read")
        c.assert_can_touch(owner_id=COLLEAGUE, team_owner_id=ME)
        with pytest.raises(ForbiddenProblem):
            c.assert_can_touch(owner_id=COLLEAGUE, team_owner_id=uuid.uuid4())

    def test_team_scope_rejects_a_record_with_no_manager(self) -> None:
        c = ctx(["x.y.z.read:TEAM"], "x.y.z.read")
        with pytest.raises(ForbiddenProblem):
            c.assert_can_touch(owner_id=COLLEAGUE, team_owner_id=None)

    def test_location_scope_compares_locations(self) -> None:
        # The regression this exists for: returning early here let a location-scoped
        # caller touch any object in the organization.
        c = ctx(["x.y.z.read:LOCATION"], "x.y.z.read", locations=["MUM", "PUN"])
        c.assert_can_touch(owner_id=COLLEAGUE, location_code="PUN")
        with pytest.raises(ForbiddenProblem):
            c.assert_can_touch(owner_id=COLLEAGUE, location_code="DEL")

    def test_location_scope_without_a_location_is_refused(self) -> None:
        c = ctx(["x.y.z.read:LOCATION"], "x.y.z.read", locations=["MUM"])
        with pytest.raises(ForbiddenProblem):
            c.assert_can_touch(owner_id=COLLEAGUE, location_code=None)


class TestScopeInjection:
    owner: Any = column("owner_id")
    manager: Any = column("manager_id")
    loc: Any = Column("location_code", String)

    def test_org_scope_returns_the_statement_untouched(self) -> None:
        stmt = select(self.owner)
        out = ctx(["x.y.z.read:ORG"], "x.y.z.read").apply(stmt, owner_col=self.owner)
        assert out is stmt

    def test_own_scope_filters_to_the_subject(self) -> None:
        out = ctx(["x.y.z.read:OWN"], "x.y.z.read").apply(select(self.owner), owner_col=self.owner)
        assert out.whereclause is not None
        assert "owner_id" in str(out)

    def test_own_scope_with_no_subject_matches_nothing(self) -> None:
        # A principal with no product subject must not fall through to "everything".
        c = ctx(["x.y.z.read:OWN"], "x.y.z.read", subject=None)
        out = c.apply(select(self.owner), owner_col=self.owner)
        assert "false" in str(out).lower()

    def test_team_scope_without_a_team_column_is_refused(self) -> None:
        c = ctx(["x.y.z.read:TEAM"], "x.y.z.read")
        with pytest.raises(ForbiddenProblem, match="TEAM scope"):
            c.apply(select(self.owner), owner_col=self.owner)

    def test_team_scope_filters_to_the_subject(self) -> None:
        out = ctx(["x.y.z.read:TEAM"], "x.y.z.read").apply(
            select(self.owner), owner_col=self.owner, team_col=self.manager
        )
        assert out.whereclause is not None
        assert "manager_id" in str(out)

    def test_team_scope_with_no_subject_matches_nothing(self) -> None:
        """The OWN guard's mirror. `manager_id == None` renders as `IS NULL`, which is every
        unmanaged row in the tenant — a TEAM grant on a membership never linked to an
        employee record must see nothing, not the whole management chain's roots."""
        c = ctx(["x.y.z.read:TEAM"], "x.y.z.read", subject=None)
        out = c.apply(select(self.owner), owner_col=self.owner, team_col=self.manager)
        rendered = str(out).lower()
        assert "false" in rendered
        assert "is null" not in rendered

    def test_location_scope_without_a_location_column_is_refused(self) -> None:
        c = ctx(["x.y.z.read:LOCATION"], "x.y.z.read", locations=["MUM"])
        with pytest.raises(ForbiddenProblem, match="LOCATION scope"):
            c.apply(select(self.owner), owner_col=self.owner)


def _operator(
    roles: list[str], *, audience: str = "operator", amr: list[str] | None = None
) -> Principal:
    return Principal(
        sub=uuid.uuid4(),
        organization_id=uuid.UUID(int=0),
        audience=audience,
        roles=roles,
        # Every real operator token carries `otp` once sign-in actually completes (MFA is
        # mandatory for staff). Defaulted on here so tests that are about the audience/role
        # axes don't also have to spell out the amr axis every time.
        amr=amr if amr is not None else ["pwd", "otp"],
    )


class TestRequireOperator:
    """The operator-side `require()`. ADR-0003 §2 plus the addendum's role matrix."""

    def test_a_product_audience_token_is_404_not_403(self) -> None:
        # The gateway's own rule, repeated in-service: a non-staff caller must not be able to
        # tell the difference between "wrong role" and "this surface does not exist".
        dep = require_operator(roles={"owner"})
        with pytest.raises(NotFoundProblem):
            dep(_operator(["owner"], audience="app"))

    def test_no_role_filter_admits_any_operator(self) -> None:
        dep = require_operator()
        assert dep(_operator(["support"])).roles == ["support"]

    def test_a_role_outside_the_set_is_a_plain_403(self) -> None:
        # Right audience — the surface's existence is already established — so this is an
        # ordinary Forbidden, not a 404.
        dep = require_operator(roles={"owner", "billing_ops"})
        with pytest.raises(ForbiddenProblem):
            dep(_operator(["support"]))

    def test_holding_any_one_of_the_required_roles_is_enough(self) -> None:
        dep = require_operator(roles={"owner", "billing_ops"})
        assert dep(_operator(["billing_ops", "support"])).audience == "operator"

    def test_an_operator_token_with_no_completed_second_factor_is_refused(self) -> None:
        """A token minted before MFA was mandatory, or one that reached this dependency some
        other way, must not pass merely for carrying the right audience and role."""
        dep = require_operator(roles={"owner"})
        with pytest.raises(ForbiddenProblem):
            dep(_operator(["owner"], amr=["pwd"]))

    def test_no_amr_at_all_is_refused_the_same_way(self) -> None:
        dep = require_operator()
        with pytest.raises(ForbiddenProblem):
            dep(_operator(["owner"], amr=[]))


#: A caller's membership id. `ME` above is the same person's employee link; the two differ, so a
#: comparison against the wrong one cannot pass by accident.
MEMBER = uuid.uuid4()


def membership_ctx(
    scope: Scope,
    *,
    membership_id: uuid.UUID | None = MEMBER,
    subject_ref: uuid.UUID | None = ME,
    locations: list[str] | None = None,
) -> ScopeContext:
    """A context for a product that declared `OwnerSubject.MEMBERSHIP`."""
    principal = Principal(
        sub=uuid.uuid4(),
        organization_id=ORG,
        membership_id=membership_id,
        subject_ref=subject_ref,
        locations=locations or [],
    )
    return ScopeContext(
        principal=principal,
        scope=scope,
        permission="x.y.z.read",
        owner_subject=OwnerSubject.MEMBERSHIP,
    )


class TestMembershipSubject:
    """OWN and TEAM on a membership context compare the membership id, and only that."""

    owner: Any = column("owner_id")
    manager: Any = column("manager_id")
    loc: Any = column("location_code")

    # ---- apply: lists

    def test_own_binds_only_the_callers_membership_id(self) -> None:
        out = membership_ctx(Scope.OWN).apply(select(self.owner), owner_col=self.owner)
        # One bound parameter, and it is the membership id: the employee link, the user id and a
        # colleague's id appear nowhere in the statement.
        assert list(out.compile().params.values()) == [MEMBER]

    def test_own_matches_a_rep_who_has_no_employee_link_at_all(self) -> None:
        # The CRM-only tenant: no HRMS, so no employee link, and the rep still owns rows.
        ctx = membership_ctx(Scope.OWN, subject_ref=None)
        out = ctx.apply(select(self.owner), owner_col=self.owner)
        assert list(out.compile().params.values()) == [MEMBER]

    def test_own_with_no_membership_id_matches_nothing_even_with_an_employee_link(self) -> None:
        out = membership_ctx(Scope.OWN, membership_id=None).apply(
            select(self.owner), owner_col=self.owner
        )
        rendered = str(out.compile()).lower()
        assert "false" in rendered
        assert "is null" not in rendered
        assert out.compile().params == {}  # nothing was compared with anything

    def test_team_binds_only_the_callers_membership_id(self) -> None:
        out = membership_ctx(Scope.TEAM).apply(
            select(self.owner), owner_col=self.owner, team_col=self.manager
        )
        assert set(out.compile().params.values()) == {MEMBER}

    def test_team_with_no_membership_id_matches_nothing(self) -> None:
        out = membership_ctx(Scope.TEAM, membership_id=None).apply(
            select(self.owner), owner_col=self.owner, team_col=self.manager
        )
        rendered = str(out.compile()).lower()
        assert "false" in rendered
        assert "is null" not in rendered
        assert out.compile().params == {}

    def test_team_without_a_team_column_is_still_refused(self) -> None:
        with pytest.raises(ForbiddenProblem, match="TEAM scope"):
            membership_ctx(Scope.TEAM).apply(select(self.owner), owner_col=self.owner)

    def test_org_returns_the_same_statement(self) -> None:
        stmt = select(self.owner)
        ctx = membership_ctx(Scope.ORG, membership_id=None, subject_ref=None)
        assert ctx.apply(stmt, owner_col=self.owner) is stmt

    def test_location_is_untouched_by_the_subject(self) -> None:
        ctx = membership_ctx(
            Scope.LOCATION, membership_id=None, subject_ref=None, locations=["MUM"]
        )
        out = ctx.apply(select(self.owner), owner_col=self.owner, location_col=self.loc)
        assert "location_code IN" in str(out.compile())
        with pytest.raises(ForbiddenProblem, match="LOCATION scope"):
            ctx.apply(select(self.owner), owner_col=self.owner)

    # ---- assert_can_touch: the refusals first

    def test_own_refuses_a_row_owned_by_the_employee_link_value(self) -> None:
        # A row an HR-linked rep wrote before the product opted in carries their employee id.
        # The membership subject must not recognise it: that is the whole point of the subject.
        with pytest.raises(ForbiddenProblem):
            membership_ctx(Scope.OWN).assert_can_touch(owner_id=ME)

    def test_own_refuses_a_row_owned_by_a_colleague(self) -> None:
        with pytest.raises(ForbiddenProblem):
            membership_ctx(Scope.OWN).assert_can_touch(owner_id=COLLEAGUE)

    def test_own_with_no_membership_id_refuses_everything(self) -> None:
        ctx = membership_ctx(Scope.OWN, membership_id=None)
        for owner_id in (None, ME, MEMBER, COLLEAGUE):
            with pytest.raises(ForbiddenProblem):
                ctx.assert_can_touch(owner_id=owner_id)

    def test_own_allows_exactly_the_callers_membership_id(self) -> None:
        membership_ctx(Scope.OWN).assert_can_touch(owner_id=MEMBER)
        membership_ctx(Scope.OWN, subject_ref=None).assert_can_touch(owner_id=MEMBER)

    def test_team_allows_a_row_whose_team_owner_is_the_callers_membership_id(self) -> None:
        membership_ctx(Scope.TEAM).assert_can_touch(owner_id=COLLEAGUE, team_owner_id=MEMBER)

    def test_team_refuses_a_row_whose_team_owner_is_the_employee_link_value(self) -> None:
        with pytest.raises(ForbiddenProblem):
            membership_ctx(Scope.TEAM).assert_can_touch(owner_id=COLLEAGUE, team_owner_id=ME)

    def test_team_refuses_when_both_ids_are_none(self) -> None:
        # No membership id, and a row with no team owner: `None == None` must not admit it.
        ctx = membership_ctx(Scope.TEAM, membership_id=None)
        with pytest.raises(ForbiddenProblem):
            ctx.assert_can_touch(owner_id=None, team_owner_id=None)

    def test_location_object_check_is_untouched_by_the_subject(self) -> None:
        ctx = membership_ctx(
            Scope.LOCATION, membership_id=None, subject_ref=None, locations=["MUM"]
        )
        ctx.assert_can_touch(owner_id=COLLEAGUE, location_code="MUM")
        with pytest.raises(ForbiddenProblem):
            ctx.assert_can_touch(owner_id=COLLEAGUE, location_code="DEL")


class TestMembershipTeamIncludesOwnRows:
    """D-24 (accepted default, ADR-0038): TEAM on a membership means "my own rows or my direct
    reports'". `Principal.scope_for` returns the widest grant only, so a manager holding TEAM write
    has no OWN grant to fall back on: without this they could not touch a record they own
    themselves. The employee-link TEAM path is deliberately left as it is.

    If the owner answers D-24 no, delete this class and the `or_` branches it pins in
    `ScopeContext.apply` and `ScopeContext.assert_can_touch`; nothing else changes."""

    owner: Any = column("owner_id")
    manager: Any = column("manager_id")

    def test_team_is_owner_or_team_column_for_a_membership(self) -> None:
        out = membership_ctx(Scope.TEAM).apply(
            select(self.owner), owner_col=self.owner, team_col=self.manager
        )
        compiled = out.compile()
        sql = str(compiled)
        assert " OR " in sql
        assert "owner_id = " in sql
        assert "manager_id = " in sql
        # Both comparisons are against the membership id, and nothing else is bound.
        assert list(compiled.params.values()) == [MEMBER, MEMBER]

    def test_team_allows_a_direct_report_or_the_callers_own_row(self) -> None:
        ctx = membership_ctx(Scope.TEAM)
        ctx.assert_can_touch(owner_id=COLLEAGUE, team_owner_id=MEMBER)  # a direct report's row
        ctx.assert_can_touch(owner_id=MEMBER, team_owner_id=None)  # the caller's own row
        ctx.assert_can_touch(owner_id=MEMBER, team_owner_id=COLLEAGUE)  # own, under another manager

    def test_team_refuses_a_row_with_neither(self) -> None:
        ctx = membership_ctx(Scope.TEAM)
        for owner_id, team_owner_id in (
            (COLLEAGUE, COLLEAGUE),
            (COLLEAGUE, None),
            (ME, ME),  # the employee-link value is not the membership subject's id
            (None, None),
        ):
            with pytest.raises(ForbiddenProblem):
                ctx.assert_can_touch(owner_id=owner_id, team_owner_id=team_owner_id)

    def test_the_employee_link_team_path_is_unchanged(self) -> None:
        principal = Principal(sub=uuid.uuid4(), organization_id=ORG, subject_ref=ME)
        ctx = ScopeContext(
            principal=principal,
            scope=Scope.TEAM,
            permission="x.y.z.read",
            owner_subject=OwnerSubject.EMPLOYEE_LINK,
        )
        out = ctx.apply(select(self.owner), owner_col=self.owner, team_col=self.manager)
        assert " OR " not in str(out.compile())
        assert list(out.compile().params.values()) == [ME]
        # The caller's own row is not admitted by a TEAM grant on this subject.
        with pytest.raises(ForbiddenProblem):
            ctx.assert_can_touch(owner_id=ME, team_owner_id=COLLEAGUE)
