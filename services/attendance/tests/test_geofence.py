"""The field-attendance geo-fence — a PostGIS distance check, against real PostgreSQL.

This is the whole point of the slice's "basic field attendance": a field punch inside the
allowed radius succeeds, one outside is refused, and a punch that is not a field punch ignores
the fence entirely. The containment test is `ST_DWithin` over `geography` run in the check-in
service — no app-side haversine, no geoalchemy2, no geometry column (CLAUDE.md, plan A.4). The
anti-spoofing stack (device, IP, mock-location) is deliberately out of scope here.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.schemas import CheckInIn, GeofenceIn
from platform_core.auth import Scope
from platform_core.errors import BusinessRuleProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

# A fence in Mumbai, 200 metres around the centre. The "inside" point is a few metres off the
# centre; the "outside" point is a couple of kilometres away — comfortably beyond the radius.
CENTER_LAT = Decimal("19.076000")
CENTER_LNG = Decimal("72.877000")
INSIDE_LAT = Decimal("19.076100")
INSIDE_LNG = Decimal("72.877100")
OUTSIDE_LAT = Decimal("19.100000")
OUTSIDE_LNG = Decimal("72.900000")


async def _seed_fence(session: AsyncSession, org_id: uuid.UUID, location_code: str) -> None:
    admin = ctx_for(org_id, uuid.uuid4(), "hrms.time.shift.write", Scope.ORG)
    async with session.begin():
        await services.create_geofence(
            session,
            admin,
            GeofenceIn(
                location_code=location_code,
                name="Plant gate",
                center_lat=CENTER_LAT,
                center_lng=CENTER_LNG,
                radius_m=200,
            ),
        )


async def _field_check_in(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    location_code: str,
    lat: Decimal | None,
    lng: Decimal | None,
) -> None:
    employee = uuid.uuid4()
    ctx = ctx_for(org_id, employee, "hrms.time.attendance.write", Scope.OWN)
    async with session.begin():
        await services.check_in(
            session,
            ctx,
            CheckInIn(
                work_mode="field", source="mobile", location_code=location_code, lat=lat, lng=lng
            ),
        )


class TestFieldPunchGeoFence:
    async def test_a_punch_inside_the_fence_succeeds(self, session, org_id) -> None:
        await _seed_fence(session, org_id, "PLANT")
        await _field_check_in(
            session, org_id, location_code="PLANT", lat=INSIDE_LAT, lng=INSIDE_LNG
        )
        # The record exists, so the punch was accepted.
        ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.attendance.read", Scope.ORG)
        rows = (await session.execute(selectors.attendance_stmt(ctx))).scalars().all()
        assert len(rows) == 1
        assert rows[0].location_code == "PLANT"

    async def test_a_punch_outside_the_fence_is_refused(self, session, org_id) -> None:
        await _seed_fence(session, org_id, "PLANT")
        with pytest.raises(BusinessRuleProblem, match="outside the allowed area for PLANT"):
            await _field_check_in(
                session, org_id, location_code="PLANT", lat=OUTSIDE_LAT, lng=OUTSIDE_LNG
            )

    async def test_a_field_punch_with_no_fix_is_refused(self, session, org_id) -> None:
        """A field punch at a fenced location cannot be placed without a coordinate."""
        await _seed_fence(session, org_id, "PLANT")
        with pytest.raises(BusinessRuleProblem, match="requires a location fix"):
            await _field_check_in(session, org_id, location_code="PLANT", lat=None, lng=None)

    async def test_a_non_field_punch_ignores_the_fence(self, session, org_id) -> None:
        """An office punch at a fenced location is never distance-checked — even from far away."""
        await _seed_fence(session, org_id, "PLANT")
        employee = uuid.uuid4()
        ctx = ctx_for(org_id, employee, "hrms.time.attendance.write", Scope.OWN)
        async with session.begin():
            await services.check_in(
                session,
                ctx,
                CheckInIn(
                    work_mode="office",
                    source="web",
                    location_code="PLANT",
                    lat=OUTSIDE_LAT,
                    lng=OUTSIDE_LNG,
                ),
            )
        read = ctx_for(org_id, uuid.uuid4(), "hrms.time.attendance.read", Scope.ORG)
        rows = (await session.execute(selectors.attendance_stmt(read))).scalars().all()
        assert len(rows) == 1

    async def test_a_field_punch_where_no_fence_exists_is_allowed(self, session, org_id) -> None:
        """The fence is enforced only where one is defined; an unfenced location does not block
        a field punch."""
        await _field_check_in(
            session, org_id, location_code="OPENYARD", lat=OUTSIDE_LAT, lng=OUTSIDE_LNG
        )
        read = ctx_for(org_id, uuid.uuid4(), "hrms.time.attendance.read", Scope.ORG)
        rows = (await session.execute(selectors.attendance_stmt(read))).scalars().all()
        assert len(rows) == 1
