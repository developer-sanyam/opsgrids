"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.

A CRM caller's `subject_ref` is their rep id: OWN scope reads the deals / activities whose
`owner_id` is that id. The helpers take a bare `subject_ref` uuid.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import conversion_ids
from app.models import Activity
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.events import validate_event_schema

#: `tests`, `deal`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


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


def aborted_envelope(
    *,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID | None = None,
    generation: int = 0,
    reason: str = "status_changed",
    claim_id: uuid.UUID | None = None,
    with_contact: bool = True,
    returned: dict[str, uuid.UUID] | None = None,
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """A `crm.lead.conversion_aborted` envelope as lead publishes it, its payload validated
    against the committed schema before it is returned. The three ids default to the ones
    `conversion_ids(lead_id, generation)` derives; `returned` overrides any of them with the ids
    customer or deal *returned*. A test that needs a malformed event mutates the result."""
    lead_id = lead_id or uuid.uuid4()
    company_id, contact_id, deal_id = conversion_ids(lead_id, generation)
    ids: dict[str, uuid.UUID | None] = {
        "company_id": company_id,
        "contact_id": contact_id if with_contact else None,
        "deal_id": deal_id,
        **(returned or {}),
    }
    payload: dict[str, Any] = {
        "organization_id": str(organization_id),
        "lead_id": str(lead_id),
        "reason": reason,
        "generation": generation,
        "claim_id": str(claim_id or uuid.uuid4()),
        **{key: str(value) if value is not None else None for key, value in ids.items()},
    }
    validate_event_schema("crm.lead.conversion_aborted", 1, payload, contracts_root=CONTRACTS_ROOT)
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "crm.lead.conversion_aborted",
        "organization_id": str(organization_id),
        "payload": payload,
    }
