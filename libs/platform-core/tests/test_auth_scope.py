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

from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import ForbiddenProblem

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

    def test_location_scope_without_a_location_column_is_refused(self) -> None:
        c = ctx(["x.y.z.read:LOCATION"], "x.y.z.read", locations=["MUM"])
        with pytest.raises(ForbiddenProblem, match="LOCATION scope"):
            c.apply(select(self.owner), owner_col=self.owner)
