"""Consumer idempotency (`docs/insights/events.md` §1): `already_processed` before acting,
`mark_processed` in the same transaction — a redelivered `core.org.*` event folds once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers
from app.models import OrgProfile, ProcessedEvent

pytestmark = pytest.mark.integration


def _envelope(
    *,
    event_id: uuid.UUID,
    event_type: str,
    organization_id: uuid.UUID,
    occurred_at: datetime,
    **payload: object,
) -> dict[str, object]:
    return {
        "event_id": str(event_id),
        "event_type": event_type,
        "organization_id": str(organization_id),
        "occurred_at": occurred_at.isoformat(),
        "payload": {"organization_id": str(organization_id), **payload},
    }


@pytest.fixture(autouse=True)
def _bind_test_session_scope(work_scope) -> None:
    consumers.use_session_scope(work_scope)
    yield
    consumers.use_session_scope(consumers._default_session_scope)


async def test_org_created_folds_into_org_profile(
    org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    occurred_at = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    await consumers.on_org_created(
        _envelope(
            event_id=uuid.uuid4(),
            event_type=consumers.EVT_ORG_CREATED,
            organization_id=org_id,
            occurred_at=occurred_at,
            timezone="Asia/Kolkata",
            currency="INR",
            fiscal_year_start_month=4,
        )
    )
    row = (
        await admin_session.execute(select(OrgProfile).where(OrgProfile.organization_id == org_id))
    ).scalar_one()
    assert row.timezone == "Asia/Kolkata"
    assert row.currency == "INR"


async def test_redelivery_is_idempotent(org_id: uuid.UUID, admin_session: AsyncSession) -> None:
    event_id = uuid.uuid4()
    occurred_at = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    envelope = _envelope(
        event_id=event_id,
        event_type=consumers.EVT_ORG_UPDATED,
        organization_id=org_id,
        occurred_at=occurred_at,
        timezone="America/New_York",
        currency="USD",
        fiscal_year_start_month=1,
    )

    await consumers.on_org_updated(envelope)
    await consumers.on_org_updated(envelope)  # redelivered, same event_id

    processed = (
        await admin_session.execute(
            select(func.count())
            .select_from(ProcessedEvent)
            .where(ProcessedEvent.event_id == event_id)
        )
    ).scalar_one()
    assert processed == 1


async def test_a_tenant_mismatch_between_envelope_and_payload_is_dropped(
    org_id: uuid.UUID, other_org: uuid.UUID, admin_session: AsyncSession
) -> None:
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": consumers.EVT_ORG_CREATED,
        "organization_id": str(org_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        # The payload names a *different* organization than the envelope.
        "payload": {
            "organization_id": str(other_org),
            "timezone": "Asia/Kolkata",
            "currency": "INR",
            "fiscal_year_start_month": 4,
        },
    }
    await consumers.on_org_created(envelope)  # must not raise, and must write nothing

    rows = (await admin_session.execute(select(OrgProfile))).scalars().all()
    assert rows == []


async def test_a_field_missing_from_an_un_upgraded_producer_raises(org_id: uuid.UUID) -> None:
    """`docs/insights/events.md` §1: a payload missing a field this consumer needs raises, so
    the message dead-letters rather than writing a wrong default."""
    envelope = _envelope(
        event_id=uuid.uuid4(),
        event_type=consumers.EVT_ORG_CREATED,
        organization_id=org_id,
        occurred_at=datetime.now(UTC),
        timezone="Asia/Kolkata",
        # currency and fiscal_year_start_month are missing.
    )
    with pytest.raises(KeyError):
        await consumers.on_org_created(envelope)
