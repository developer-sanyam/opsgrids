"""Shared builders for the anti-spoofing tests (R22): a request as the service sees one behind the
gateway, the organization's rules saved the way an administrator saves them, punches through the
real write path, and the history the travel and shared-device rules read.

Kept apart from `helpers.py` because these reach into the service (`services`, `signals`) where that
module stays free of it, and shared by `test_signals.py` (the rules) and `test_signal_routes.py`
(who may see and reveal what they produce).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import services, signals
from app.models import AttendanceFlag, AttendanceRecord, PunchSignal
from app.schemas import CheckInIn, CheckOutIn, SignalSettingsIn
from platform_core.auth import Scope
from tests.helpers import ctx_for

MUMBAI = (Decimal("19.076000"), Decimal("72.877700"))
DELHI = (Decimal("28.613900"), Decimal("77.209000"))
#: About five kilometres north of `MUMBAI`.
NEAR_MUMBAI = (Decimal("19.121000"), Decimal("72.877700"))

# Opaque client-side device hashes for the tests; not credentials.
DEVICE_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90"  # pragma: allowlist secret
DEVICE_B = "0f9e8d7c6b5a49382716f5e4d3c2b1a0"  # pragma: allowlist secret

_past_day = iter(range(1, 10_000))


def make_request(
    *, xff: str | None = None, ua: str | None = None, client: str = "10.0.0.1"
) -> Request:
    """A request as the service sees one behind the gateway: `X-Forwarded-For` and `User-Agent`
    are the only headers the signals read."""
    headers: list[tuple[bytes, bytes]] = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if ua is not None:
        headers.append((b"user-agent", ua.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/attendance/check-in",
            "headers": headers,
            "client": (client, 5000),
            "query_string": b"",
        }
    )


async def configure(session: AsyncSession, org_id: uuid.UUID, **fields: Any) -> None:
    """Save the organization's rules the way an administrator would (`shift.write:ORG`)."""
    ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.shift.write", Scope.ORG)
    async with session.begin():
        await signals.put_settings(session, ctx, SignalSettingsIn(**fields))


async def check_in(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    request: Request | None = None,
    work_mode: str = "office",
    **signal: Any,
) -> AttendanceRecord:
    ctx = ctx_for(org_id, employee_id, "hrms.time.attendance.write", Scope.OWN)
    payload = CheckInIn(work_mode=work_mode, source="web", **signal)
    async with session.begin():
        return await services.check_in(session, ctx, payload, request=request)


async def check_out(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    request: Request | None = None,
    **signal: Any,
) -> AttendanceRecord:
    ctx = ctx_for(org_id, employee_id, "hrms.time.attendance.write", Scope.OWN)
    async with session.begin():
        return await services.check_out(session, ctx, CheckOutIn(**signal), request=request)


async def seed_signal(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    ago: timedelta,
    lat: Decimal | None = None,
    lng: Decimal | None = None,
    accuracy: Decimal | None = None,
    device: str | None = None,
    kind: str = "check_in",
) -> None:
    """A punch's signal from earlier — the history the travel and shared-device rules read. The
    record it belongs to gets its own long-past day, so it never collides with today's punch."""
    when = datetime.now(UTC) - ago
    record_id = uuid.uuid4()
    async with session.begin():
        session.add(
            AttendanceRecord(
                id=record_id,
                organization_id=org_id,
                employee_id=employee_id,
                work_date=date(2020, 1, 1) + timedelta(days=next(_past_day)),
                check_in_at=when,
                source="web",
                work_mode="office",
                status="present",
            )
        )
        await session.flush()
        session.add(
            PunchSignal(
                id=uuid.uuid4(),
                organization_id=org_id,
                attendance_id=record_id,
                employee_id=employee_id,
                kind=kind,
                punched_at=when,
                lat=lat,
                lng=lng,
                accuracy_m=accuracy,
                device_index=signals.device_index_for(device) if device else None,
            )
        )


async def all_flags(session: AsyncSession, reason: str | None = None) -> list[AttendanceFlag]:
    stmt = select(AttendanceFlag).order_by(AttendanceFlag.created_at)
    if reason:
        stmt = stmt.where(AttendanceFlag.reason_code == reason)
    async with session.begin():
        return list((await session.execute(stmt)).scalars().all())


async def all_signals(session: AsyncSession) -> list[PunchSignal]:
    async with session.begin():
        return list((await session.execute(select(PunchSignal))).scalars().all())
