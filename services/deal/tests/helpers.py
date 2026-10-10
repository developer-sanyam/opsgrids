"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.

A CRM caller's `subject_ref` is their rep id: OWN scope reads the deals / activities whose
`owner_id` is that id. The helpers take a bare `subject_ref` uuid.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity
from platform_core.auth import Principal, Scope, ScopeContext


def principal_for(
    org_id: uuid.UUID,
    perms: list[str],
    *,
    subject_ref: uuid.UUID | None = None,
    entitlements: list[str] | None = None,
    locations: list[str] | None = None,
    amr: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded for a CRM caller."""
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        subject_ref=subject_ref,
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["crm"],
        locations=locations or [],
        amr=amr or [],
    )


def ctx_for(
    org_id: uuid.UUID,
    permission: str,
    scope: Scope,
    *,
    subject_ref: uuid.UUID | None = None,
    locations: list[str] | None = None,
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(
        org_id, [f"{permission}:{scope.value}"], subject_ref=subject_ref, locations=locations
    )
    return ScopeContext(principal=principal, scope=scope, permission=permission)


async def activities_for_deal(
    session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID
) -> list[Activity]:
    """One deal's timeline, newest first, for assertions. It lived in `app/selectors.py` until
    nothing but tests called it; the service reads timelines through `selectors.activities_stmt`,
    which applies the caller's scope."""
    stmt = (
        select(Activity)
        .where(
            Activity.organization_id == org_id,
            Activity.deal_id == deal_id,
            Activity.deleted_at.is_(None),
        )
        .order_by(Activity.occurred_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())
