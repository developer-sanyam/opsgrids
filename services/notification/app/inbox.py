"""Per-user in-app notification feed — `GET /notifications/me` and its two mark-read routes.

Reuses `models.Message` on `channel == domain.IN_APP_CHANNEL` rather than a new table (see that
class's docstring, and `domain.CHANNELS`): a rule staged an in-app delivery the same way it stages
an email or a WhatsApp one, `transports.InAppTransport` "sends" it by doing nothing, and the row
the dispatcher leaves at `state == "sent"` is the whole of the delivery. `to_address` holds the
recipient's **membership id**, and `read_at`/`state` already carry the one new idea a feed needs.

Both reads and the two writes below live in this one module rather than split across
`selectors.py`/`services.py` — deliberately, for this slice: nothing here reaches into either of
those files, and nothing there should reach into this one.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import ColumnElement, CursorResult, func, literal, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.models import Message
from platform_core.auth import Principal
from platform_core.errors import ForbiddenProblem, NotFoundProblem, ValidationProblem

#: A page smaller than the admin lists elsewhere in this service (`console`'s 25/200) on
#: purpose: this is a bell-icon feed, not a report.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

#: Unit separator — never a legal character in an ISO timestamp or a UUID, so joining and
#: splitting on it is unambiguous without escaping. The same idea as `console/app/pagination.py`'s
#: cursor, reimplemented here rather than imported: one service's `app` package is never another's
#: dependency.
_SEP = "\x1f"


class NotificationOut(BaseModel):
    id: uuid.UUID
    category: str
    subject: str | None
    body: str
    event_type: str | None
    rule_code: str | None
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None
    #: Folded from the same filter as the page, minus the cursor and `unread_only` — so a bell
    #: badge never needs a request of its own.
    unread_count: int


class ReadAllOut(BaseModel):
    updated: int


def encode_cursor(created_at: datetime, message_id: uuid.UUID) -> str:
    """Pack the last row's ordering columns into one opaque token."""
    raw = f"{created_at.isoformat()}{_SEP}{message_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Unpack a token this module produced. Never trusted blindly — a caller could hand back a
    stale, doctored or foreign one, so a malformed cursor is a 400, not a 500 two frames later."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        raw_at, raw_id = raw.split(_SEP)
        return datetime.fromisoformat(raw_at), uuid.UUID(raw_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationProblem("Invalid cursor") from exc


def recipient_id_for(principal: Principal) -> uuid.UUID:
    """Whose feed this is — the caller's own membership, the same identity a rule's `in_app`
    delivery targets (`domain.normalize_recipient`). Never the bare, cross-organization user id:
    a membership is what `ADR-0000 §1` scopes to one organization, and this feed is read through
    a tenant-bound session already filtered to one. An operator token carries no membership at
    all and so has no feed here — refused rather than shown an empty one, so the difference is
    visible to whoever is debugging why a staff sign-in cannot see it.
    """
    if principal.membership_id is None:
        raise ForbiddenProblem("This caller holds no membership to have a feed against")
    return principal.membership_id


def _out(row: Message) -> NotificationOut:
    return NotificationOut(
        id=row.id,
        category=row.category,
        subject=row.subject,
        body=row.body,
        event_type=row.event_type,
        rule_code=row.rule_code,
        created_at=row.created_at,
        read_at=row.read_at,
    )


def _base_filter(
    *, organization_id: uuid.UUID, recipient_id: uuid.UUID
) -> list[ColumnElement[bool]]:
    """The predicate every read and write below starts from: one recipient's own in-app
    messages that actually reached the feed — `domain.IN_APP_VISIBLE_STATES` excludes a still-
    `pending` row nobody has dispatched yet and a `failed`/`suppressed` one nobody was shown."""
    return [
        Message.organization_id == organization_id,
        Message.channel == domain.IN_APP_CHANNEL,
        Message.to_address == str(recipient_id),
        Message.state.in_(domain.IN_APP_VISIBLE_STATES),
        Message.deleted_at.is_(None),
    ]


async def list_for_recipient(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    recipient_id: uuid.UUID,
    limit: int,
    cursor: str | None,
    unread_only: bool,
) -> NotificationPage:
    """One recipient's own feed, newest first, keyset-paged on `(created_at, id)` — several
    notifications can land in the same request-second, so `id` is the tiebreaker a plain
    `created_at` cursor would otherwise skip or repeat rows around."""
    stmt = (
        select(Message)
        .where(*_base_filter(organization_id=organization_id, recipient_id=recipient_id))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(Message.read_at.is_(None))
    if cursor is not None:
        after_at, after_id = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Message.created_at, Message.id) < tuple_(literal(after_at), literal(after_id))
        )

    rows = (await session.execute(stmt)).scalars().all()
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if len(rows) == limit else None

    unread_stmt = select(func.count()).where(
        *_base_filter(organization_id=organization_id, recipient_id=recipient_id),
        Message.read_at.is_(None),
    )
    unread = (await session.execute(unread_stmt)).scalar_one()

    return NotificationPage(
        items=[_out(row) for row in rows],
        next_cursor=next_cursor,
        unread_count=int(unread),
    )


async def mark_read(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    recipient_id: uuid.UUID,
    message_id: uuid.UUID,
    actor: str,
) -> NotificationOut:
    """Mark one of the caller's own notifications read. Idempotent: reading it twice is not an
    error, and the second call simply reports the same row back.

    `to_address` is part of the lookup's own `WHERE`, not a check applied after the fact — a
    notification addressed to someone else looks exactly like one that does not exist, which is
    what keeps this from being a way to learn a stranger's notification id is valid.
    """
    stmt = select(Message).where(
        Message.id == message_id,
        *_base_filter(organization_id=organization_id, recipient_id=recipient_id),
    )
    message = (await session.execute(stmt)).scalar_one_or_none()
    if message is None:
        raise NotFoundProblem("No notification with that id")

    if message.read_at is None:
        message.read_at = datetime.now(UTC)
        message.state = domain.STATE_READ
        message.updated_by = actor
        await session.flush()
    return _out(message)


async def mark_all_read(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    recipient_id: uuid.UUID,
    actor: str,
) -> ReadAllOut:
    """Mark every one of the caller's own unread notifications read, in one statement rather
    than a loop over rows — the N+1 a "mark all" screen invites if written the obvious way."""
    stmt = (
        update(Message)
        .where(
            *_base_filter(organization_id=organization_id, recipient_id=recipient_id),
            Message.read_at.is_(None),
        )
        .values(read_at=datetime.now(UTC), state=domain.STATE_READ, updated_by=actor)
        .execution_options(synchronize_session=False)
    )
    # `session.execute` is typed to return the generic `Result`, but an UPDATE statement always
    # produces a `CursorResult` at runtime — which is what actually carries `rowcount` (the same
    # cast `services.py`'s own bulk updates use).
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return ReadAllOut(updated=int(result.rowcount or 0))
