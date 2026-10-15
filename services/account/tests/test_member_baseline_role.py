"""Everyone is a Member: an invitee given only a product role still holds the core MEMBER grants
(own approvals, locations, organization read), which product role templates cannot carry."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import Organization, Role


async def test_a_product_only_role_list_still_includes_the_member_role(
    session: AsyncSession, org: Organization
) -> None:
    async with session.begin():
        session.add(Role(organization_id=org.id, product="hrms", code="EMPLOYEE", name="Employee"))
        await session.flush()
        m = await services.add_member(
            session,
            org_id=org.id,
            email="emp@example.com",
            full_name="Emma Employee",
            actor_user_id=None,
            role_codes=["EMPLOYEE"],
        )
        member_role = await selectors.get_role_by_code(session, org.id, "MEMBER")
    assert member_role is not None
    assert member_role.id in {r.role_id for r in m.roles}
    assert len(m.roles) == 2
