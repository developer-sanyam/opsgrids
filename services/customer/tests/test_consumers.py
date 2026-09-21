"""`crm.pipeline.activity.logged`, consumed to advance a touched contact's
`last_activity_at` (G7).

The consumer opens its own `session_scope`, so this mirrors `services/attendance/tests/
test_consumers.py`'s shape: the process engine is initialised by an autouse fixture standing
in for the app lifespan, the handler is called directly (never through the AMQP loop), and
assertions read back through the test's own tenant-bound `session` fixture after
`session.expire_all()` — the consumer wrote through a different connection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app import selectors
from app.config import settings
from app.consumers import contact_activity_logged
from app.models import ProcessedEvent
from platform_core.db import dispose_engine, init_engine
from platform_core.events import already_processed

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer's `session_scope` needs the process engine initialised — the app
    lifespan does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


def _envelope(
    *, organization_id: uuid.UUID, contact_id: uuid.UUID | None, occurred_at: str
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "crm.pipeline.activity.logged",
        "payload": {
            "organization_id": str(organization_id),
            "activity_id": str(uuid.uuid4()),
            "type": "call",
            "deal_id": str(uuid.uuid4()),
            "contact_id": str(contact_id) if contact_id else None,
            "occurred_at": occurred_at,
        },
    }


class TestActivityLoggedTouchesTheContact:
    async def test_a_touch_advances_last_activity_at(self, session, org_id, data) -> None:
        contact_id = data["contact_b_id"]  # last_activity_at is None in the fixture
        at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        envelope = _envelope(
            organization_id=org_id, contact_id=contact_id, occurred_at=at.isoformat()
        )

        await contact_activity_logged(envelope)

        session.expire_all()
        async with session.begin():
            refreshed = await selectors.get_contact(session, org_id, contact_id)
        assert refreshed is not None
        assert refreshed.last_activity_at == at

    async def test_it_is_idempotent(self, session, org_id, data) -> None:
        contact_id = data["contact_b_id"]
        at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        envelope = _envelope(
            organization_id=org_id, contact_id=contact_id, occurred_at=at.isoformat()
        )

        await contact_activity_logged(envelope)
        await contact_activity_logged(envelope)  # redelivery

        session.expire_all()
        async with session.begin():
            assert await already_processed(session, ProcessedEvent, uuid.UUID(envelope["event_id"]))

    async def test_a_touch_never_winds_the_clock_backwards(self, session, org_id, data) -> None:
        contact_id = data["contact_a_id"]  # already touched at DEMO_NOW in the fixture
        original = data["contact_a"].last_activity_at
        earlier = datetime(2020, 1, 1, tzinfo=UTC)
        envelope = _envelope(
            organization_id=org_id, contact_id=contact_id, occurred_at=earlier.isoformat()
        )

        await contact_activity_logged(envelope)

        session.expire_all()
        async with session.begin():
            refreshed = await selectors.get_contact(session, org_id, contact_id)
        assert refreshed is not None
        assert refreshed.last_activity_at == original

    async def test_an_activity_naming_no_contact_is_a_no_op(self, session, org_id, data) -> None:
        envelope = _envelope(
            organization_id=org_id, contact_id=None, occurred_at=datetime.now(UTC).isoformat()
        )
        # Must not raise, and must still mark itself processed.
        await contact_activity_logged(envelope)
        async with session.begin():
            assert await already_processed(session, ProcessedEvent, uuid.UUID(envelope["event_id"]))

    async def test_an_unknown_contact_does_not_raise(self, session, org_id, data) -> None:
        envelope = _envelope(
            organization_id=org_id,
            contact_id=uuid.uuid4(),
            occurred_at=datetime.now(UTC).isoformat(),
        )
        await contact_activity_logged(envelope)
