"""`b3e8d5a1c7f9`: members invited with only product roles get their own organization's MEMBER,
once; anyone already holding a core role is left alone."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import MembershipRole, Organization, Role
from app.schemas import OrganizationIn

_VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
_PATH = _VERSIONS / "b3e8d5a1c7f9_backfill_member_role.py"
_spec = importlib.util.spec_from_file_location("backfill_member_role", _PATH)
assert _spec is not None and _spec.loader is not None
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


async def _legacy_employee(s: AsyncSession, org_id: uuid.UUID, email: str) -> uuid.UUID:
    """A member as the old `add_member` left them: EMPLOYEE only, no MEMBER."""
    m = await services.add_member(
        s, org_id=org_id, email=email, full_name=email, actor_user_id=None, role_codes=["EMPLOYEE"]
    )
    member = await selectors.get_role_by_code(s, org_id, "MEMBER")
    assert member is not None
    await s.execute(
        delete(MembershipRole).where(
            MembershipRole.membership_id == m.id, MembershipRole.role_id == member.id
        )
    )
    return m.id


async def _codes(s: AsyncSession, membership_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    rows = await s.execute(
        select(Role.organization_id, Role.code)
        .join(MembershipRole, MembershipRole.role_id == Role.id)
        .where(MembershipRole.membership_id == membership_id)
        .order_by(Role.code)
    )
    return [(r[0], r[1]) for r in rows]


async def test_backfill_adds_member_per_org_once_and_skips_core_holders(
    admin_session: AsyncSession, org: Organization
) -> None:
    s = admin_session
    async with s.begin():
        other = await services.create_organization(
            s, OrganizationIn(name="Beta Works", slug="beta"), actor_user_id=None
        )
        for o in (org.id, other.id):
            s.add(Role(organization_id=o, product="hrms", code="EMPLOYEE", name="Employee"))
        await s.flush()
        legacy = await _legacy_employee(s, org.id, "emp@example.com")
        legacy_other = await _legacy_employee(s, other.id, "emp@beta.example.com")
        admin = await services.add_member(
            s,
            org_id=org.id,
            email="admin@example.com",
            full_name="Ada Admin",
            actor_user_id=None,
            role_codes=["ORG_ADMIN", "EMPLOYEE"],
        )

    for _ in range(2):  # idempotent
        async with s.begin():
            await s.execute(migration.BACKFILL)

    async with s.begin():
        assert await _codes(s, legacy) == [(org.id, "EMPLOYEE"), (org.id, "MEMBER")]
        assert await _codes(s, legacy_other) == [(other.id, "EMPLOYEE"), (other.id, "MEMBER")]
        assert await _codes(s, admin.id) == [(org.id, "EMPLOYEE"), (org.id, "ORG_ADMIN")]
