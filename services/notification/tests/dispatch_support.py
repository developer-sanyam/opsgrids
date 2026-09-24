"""What the dispatch tests share: the simulated collaborators and the small readers.

Only two things are simulated, both outside the notification service — the relay and broker
(`Relay`, which hands each queued event in the outbox to the dispatcher, once, plus the duplicates a
test asks for: at-least-once is the contract) and the passage of time (`tick(now=)`). Everything
else in the tests that use this is the real dispatcher, the real sweep and real PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import dispatcher, services
from app.config import Settings, settings
from app.models import Delivery, Message, OutboxEvent
from app.transports import Outgoing, Receipt
from tests.conftest import seed_automation
from tests.helpers import envelope_for

TICKET = "support.desk.ticket.created"


class Crash(BaseException):
    """A process dying: not an `Exception`, so nothing in `dispatch` catches it, and the claim
    transaction it had already committed is all that is left — exactly a killed worker."""


class Scripted:
    """A transport that does what it is told, one send at a time, then succeeds. Counts sends —
    what "exactly once" is measured in."""

    name = "smtp"

    def __init__(self, *outcomes: BaseException | None) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0
        #: Every message handed to `send`, whatever became of it — what a header is checked on.
        self.attempted: list[Outgoing] = []
        self.delivered: list[Outgoing] = []

    async def send(self, message: Outgoing) -> Receipt:
        self.calls += 1
        self.attempted.append(message)
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        self.delivered.append(message)
        return Receipt(provider=self.name, provider_message_id=f"smtp-{len(self.delivered)}")


class Relay:
    """The relay and the broker, for the one queue the dispatcher reads: hands each
    `message.queued` event in the outbox to `dispatcher.dispatch`, once. `redeliver` hands one
    over again — the at-least-once duplicate."""

    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        transport: Scripted,
        *,
        channel: str = "email",
        cfg: Settings | None = None,
    ) -> None:
        self._session = session
        self._org_id = org_id
        self._transports = {channel: transport}
        self._cfg = cfg or settings()
        self.handed_over: list[uuid.UUID] = []

    async def queued_events(self) -> list[OutboxEvent]:
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED)
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
        )
        async with self._session.begin():
            return list((await self._session.execute(stmt)).scalars().all())

    async def pump(self) -> int:
        """Deliver every queued event not yet delivered. Returns how many."""
        sent = 0
        for row in await self.queued_events():
            if row.id in self.handed_over:
                continue
            self.handed_over.append(row.id)
            await self._deliver(row)
            sent += 1
        return sent

    async def redeliver(self, event_id: uuid.UUID) -> None:
        (row,) = [r for r in await self.queued_events() if r.id == event_id]
        await self._deliver(row)

    async def _deliver(self, row: OutboxEvent) -> None:
        envelope = envelope_for(self._org_id, row.event_type, dict(row.payload), event_id=row.id)
        await dispatcher.dispatch(envelope, transports=self._transports, cfg=self._cfg)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- helpers


async def stage_ticket(
    session: AsyncSession, org_id: uuid.UUID, *, number: str = "TK-0001"
) -> uuid.UUID:
    await seed_automation(session, org_id)
    event = envelope_for(
        org_id,
        TICKET,
        {
            "number": number,
            "requester_name": "Helena Vogel",
            "requester_email": "h@northwind.example.com",
        },
    )
    async with session.begin():
        staged = await services.apply_rules(session, organization_id=org_id, envelope=event)
    return staged[0].id


async def load_message(session: AsyncSession, message_id: uuid.UUID) -> Message:
    async with session.begin():
        row = (
            await session.execute(
                select(Message)
                .where(Message.id == message_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        session.expunge(row)
    return row


async def load_deliveries(
    session: AsyncSession, message_id: uuid.UUID
) -> list[tuple[int, str, str | None]]:
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(Delivery)
                    .where(Delivery.message_id == message_id)
                    .order_by(Delivery.attempt_no)
                )
            )
            .scalars()
            .all()
        )
    return [(r.attempt_no, r.outcome, r.detail) for r in rows]


async def load_events(session: AsyncSession, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        return list(
            (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)))
            .scalars()
            .all()
        )


def after(message: Message, seconds: float = 1.0) -> datetime:
    assert message.retry_at is not None
    return message.retry_at + timedelta(seconds=seconds)
