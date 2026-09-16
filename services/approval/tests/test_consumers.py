"""The hierarchy consumer: redelivery idempotency, the version gate through a real event
envelope, tenant binding per message, and the "no `hierarchy_version` on the payload" case
ADR-0006 §2.1 requires (see `app/consumers.py`'s module docstring for why that case matters
today).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.consumers import _apply_hierarchy_event
from app.models import ProcessedEvent
from platform_core.db import dispose_engine, init_engine
from platform_core.events import already_processed
from tests.helpers import employee_onboarded_envelope

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer opens its own `session_scope`, which needs the process engine initialised —
    the app lifespan does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _stored(session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID) -> dict | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT source_version, display_name FROM approval.hierarchy_nodes "
                    "WHERE organization_id = :org AND employee_id = :emp"
                ),
                {"org": str(org_id), "emp": str(employee_id)},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


class TestFullStateEventApplied:
    async def test_a_full_state_event_upserts_the_node(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        envelope = employee_onboarded_envelope(
            organization_id=org_id, employee_id=employee_id, hierarchy_version=1
        )

        await _apply_hierarchy_event(envelope)

        # The consumer opens its own `session_scope`, so assert through a fresh read on the
        # test's own tenant-bound session rather than the (uncommitted) fixture session.
        async with session.begin():
            row = await _stored(session, org_id, employee_id)
        assert row is not None
        assert row["source_version"] == 1


class TestRedeliveryIsIdempotent:
    async def test_the_same_event_id_delivered_twice_is_applied_once(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        event_id = uuid.uuid4()
        envelope = employee_onboarded_envelope(
            event_id=event_id,
            organization_id=org_id,
            employee_id=employee_id,
            display_name="First Delivery",
            hierarchy_version=1,
        )

        await _apply_hierarchy_event(envelope)
        # Redelivered with a different body but the *same* event id — `already_processed` must
        # stop it before the upsert ever runs, exactly as a broker redelivery would replay the
        # identical message.
        replay = dict(envelope)
        replay["payload"] = {**envelope["payload"], "display_name": "Second Delivery"}
        await _apply_hierarchy_event(replay)

        async with session.begin():
            row = await _stored(session, org_id, employee_id)
            processed = await already_processed(session, ProcessedEvent, event_id)
        assert row is not None
        assert row["display_name"] == "First Delivery"
        assert processed is True


class TestVersionGateThroughTheConsumer:
    async def test_an_older_version_delivered_later_is_dropped(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        newer = employee_onboarded_envelope(
            organization_id=org_id,
            employee_id=employee_id,
            display_name="Newer",
            hierarchy_version=5,
        )
        older = employee_onboarded_envelope(
            organization_id=org_id,
            employee_id=employee_id,
            display_name="Older",
            hierarchy_version=3,
        )

        await _apply_hierarchy_event(newer)
        await _apply_hierarchy_event(older)

        async with session.begin():
            row = await _stored(session, org_id, employee_id)
        assert row == {"source_version": 5, "display_name": "Newer"}


class TestMalformedOccurredAt:
    async def test_a_malformed_occurred_at_does_not_dead_letter_the_event(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`source_occurred_at` is display/debugging only and never compared (`HierarchyNode`'s
        docstring) — a bad value in that one field must not raise and cost the event a redelivery
        it cannot recover from, contradicting `_hierarchy_state`'s documented "logged and dropped"
        behaviour for the fields that matter more than this one."""
        employee_id = uuid.uuid4()
        envelope = employee_onboarded_envelope(
            organization_id=org_id, employee_id=employee_id, hierarchy_version=1
        )
        envelope["occurred_at"] = "not-a-timestamp"

        await _apply_hierarchy_event(envelope)

        async with session.begin():
            row = await _stored(session, org_id, employee_id)
        assert row is not None
        assert row["source_version"] == 1


class TestMissingHierarchyVersion:
    async def test_a_payload_with_no_hierarchy_version_is_ignored_not_treated_as_zero(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §2.1: "If employee returns a node whose `hierarchy_version` is absent — an
        un-upgraded pod — the row is ignored, never treated as version 0." This is the shape
        `services/employee/app/services.py` publishes today (verified while this slice was
        built), so the case is not hypothetical."""
        employee_id = uuid.uuid4()
        envelope = employee_onboarded_envelope(
            organization_id=org_id, employee_id=employee_id, hierarchy_version=None
        )

        await _apply_hierarchy_event(envelope)

        async with session.begin():
            row = await _stored(session, org_id, employee_id)
            processed = await already_processed(
                session, ProcessedEvent, uuid.UUID(envelope["event_id"])
            )
        assert row is None
        # Still marked processed: a pre-hierarchy payload is a handled outcome, not an error, and
        # must not be retried forever.
        assert processed is True

    async def test_a_later_event_with_a_version_still_applies_after_one_without(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        pre_hierarchy = employee_onboarded_envelope(
            organization_id=org_id, employee_id=employee_id, hierarchy_version=None
        )
        upgraded = employee_onboarded_envelope(
            organization_id=org_id,
            employee_id=employee_id,
            display_name="Post Upgrade",
            hierarchy_version=1,
        )

        await _apply_hierarchy_event(pre_hierarchy)
        await _apply_hierarchy_event(upgraded)

        async with session.begin():
            row = await _stored(session, org_id, employee_id)
        assert row == {"source_version": 1, "display_name": "Post Upgrade"}
