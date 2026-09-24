"""R22: capturing a punch's signals and judging them — the four rules, the off-by-default
guarantee, and how the personal parts are kept.

The properties that earn the change are the negative ones: a tenant with no settings stores
nothing and flags nothing; a rule that is off does not fire even when its signal is present; an
IPv6-mapped office address still reads as inside the office range; another employee's, another
tenant's and an unlocated punch are never mistaken for the previous one; a flag never carries an
address, a device id or a colleague's id; and no plaintext IP or device id is recoverable from the
row without the unmask route. Everything runs against real PostgreSQL under the NOBYPASSRLS app
role, through the real write path (`services.check_in` / `check_out`).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import services, signals
from app.main import app
from app.models import (
    AttendanceFlag,
    AttendanceRecord,
    AuditLog,
    PunchSignal,
    SignalSettings,
)
from app.schemas import CheckInIn, SignalSettingsIn
from platform_core.auth import Principal, Scope, get_principal
from platform_core.pii import PiiError, open_sealed
from tests.helpers import ctx_for, principal_for
from tests.signal_helpers import (
    DELHI,
    DEVICE_A,
    DEVICE_B,
    MUMBAI,
    NEAR_MUMBAI,
    all_flags,
    all_signals,
    check_in,
    check_out,
    configure,
    make_request,
    seed_signal,
)

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


@pytest.fixture
def me() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other() -> uuid.UUID:
    return uuid.uuid4()


# --------------------------------------------------------------------- off by default


class TestOffByDefault:
    async def test_a_tenant_with_no_settings_stores_nothing_and_flags_nothing(
        self, session, org_id, me
    ) -> None:
        """The whole of "does not break existing tenants": every signal below would trip a rule if
        one were on, and with none configured the punch is exactly what it was before R22."""
        record = await check_in(
            session,
            org_id,
            me,
            request=make_request(xff="198.51.100.7", ua="curl/8.4.0"),
            mock_location=True,
            device_id_hash=DEVICE_A,
            lat=MUMBAI[0],
            lng=MUMBAI[1],
        )

        assert record.status == "present"
        assert await all_signals(session) == []
        assert await all_flags(session) == []

    async def test_a_settings_row_with_every_rule_off_stores_nothing(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id)  # every field at its default: all rules off
        await check_in(
            session,
            org_id,
            me,
            request=make_request(xff="198.51.100.7"),
            mock_location=True,
            device_id_hash=DEVICE_A,
        )
        assert await all_signals(session) == []
        assert await all_flags(session) == []

    async def test_the_settings_defaults_are_all_off(self) -> None:
        defaults = SignalSettingsIn()
        assert not any(
            (
                defaults.detect_mock_location,
                defaults.detect_impossible_travel,
                defaults.detect_shared_device,
                defaults.detect_ip_outside_allowed,
            )
        )
        assert defaults.allowed_cidrs == []

    async def test_one_rule_on_stores_the_signal_but_only_that_rule_fires(
        self, session, org_id, me, other
    ) -> None:
        """Mock-location on; the same punch also carries an off-network address and a shared
        device. Rules that are off stay off even when their signal is right there."""
        await configure(session, org_id, detect_mock_location=True)
        await seed_signal(session, org_id, other, ago=timedelta(hours=1), device=DEVICE_A)

        await check_in(
            session,
            org_id,
            me,
            request=make_request(xff="198.51.100.7"),
            mock_location=True,
            device_id_hash=DEVICE_A,
        )

        assert [f.reason_code for f in await all_flags(session)] == ["mock_location"]


# --------------------------------------------------------------------- observe-only


class TestObserveOnly:
    async def test_a_flagged_punch_is_recorded_exactly_as_an_unflagged_one_is(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_mock_location=True)

        record = await check_in(
            session, org_id, me, mock_location=True, lat=MUMBAI[0], lng=MUMBAI[1]
        )

        assert record.status == "present"
        assert record.check_in_lat == MUMBAI[0]
        assert record.employee_id == me
        assert len(await all_flags(session)) == 1

    async def test_a_flagged_check_out_still_closes_the_day(self, session, org_id, me) -> None:
        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me)

        record = await check_out(session, org_id, me, mock_location=True)

        assert record.check_out_at is not None
        assert [f.reason_code for f in await all_flags(session)] == ["mock_location"]

    async def test_a_second_punch_in_the_day_is_still_refused_with_the_rules_on(
        self, session, org_id, me
    ) -> None:
        """The existing one-record-per-day rule is untouched by the signal stack."""
        from platform_core.errors import ConflictProblem

        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, mock_location=True)
        with pytest.raises(ConflictProblem):
            await check_in(session, org_id, me, mock_location=True)


# --------------------------------------------------------------------- mock location


class TestMockLocation:
    async def test_a_claimed_mock_location_is_flagged(self, session, org_id, me) -> None:
        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, mock_location=True)
        flags = await all_flags(session)
        assert [(f.reason_code, f.employee_id, f.detail) for f in flags] == [
            ("mock_location", me, {})
        ]

    @pytest.mark.parametrize("claim", [False, None])
    async def test_no_claim_or_a_denied_one_is_not_flagged(
        self, session, org_id, me, claim: bool | None
    ) -> None:
        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, mock_location=claim)
        assert await all_flags(session) == []
        # ... but the signal itself was kept, because a rule is on.
        assert len(await all_signals(session)) == 1


# --------------------------------------------------------------------- impossible travel


class TestImpossibleTravel:
    async def test_mumbai_to_delhi_two_minutes_later_is_flagged_with_the_numbers(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_impossible_travel=True)
        await seed_signal(
            session, org_id, me, ago=timedelta(minutes=2), lat=MUMBAI[0], lng=MUMBAI[1]
        )

        await check_in(session, org_id, me, lat=DELHI[0], lng=DELHI[1])

        (flag,) = await all_flags(session, "impossible_travel")
        assert flag.detail["threshold_kmh"] == 900
        assert flag.detail["from_kind"] == "check_in"
        assert flag.detail["distance_m"] == pytest.approx(1_153_000, rel=0.02)
        assert flag.detail["speed_kmh"] > 20_000
        # Measurements only.
        assert set(flag.detail) == {
            "distance_m",
            "effective_m",
            "elapsed_s",
            "speed_kmh",
            "threshold_kmh",
            "from_kind",
        }

    async def test_the_same_trip_the_next_morning_is_not(self, session, org_id, me) -> None:
        await configure(session, org_id, detect_impossible_travel=True)
        await seed_signal(
            session, org_id, me, ago=timedelta(hours=20), lat=MUMBAI[0], lng=MUMBAI[1]
        )
        await check_in(session, org_id, me, lat=DELHI[0], lng=DELHI[1])
        assert await all_flags(session) == []

    async def test_the_organizations_own_speed_ceiling_decides(self, session, org_id, me) -> None:
        """Five km in one minute is 300 km/h: fine at the default 900, impossible at 100."""
        await configure(session, org_id, detect_impossible_travel=True, max_travel_speed_kmh=100)
        await seed_signal(
            session, org_id, me, ago=timedelta(minutes=1), lat=MUMBAI[0], lng=MUMBAI[1]
        )
        await check_in(session, org_id, me, lat=NEAR_MUMBAI[0], lng=NEAR_MUMBAI[1])
        assert [f.reason_code for f in await all_flags(session)] == ["impossible_travel"]

    async def test_reported_accuracy_is_credited_before_the_speed_is_judged(
        self, session, org_id, me
    ) -> None:
        """The same five kilometres, but both fixes say "within 2 km": 1 km is left over, which is
        60 km/h, under the 100 km/h ceiling."""
        await configure(session, org_id, detect_impossible_travel=True, max_travel_speed_kmh=100)
        await seed_signal(
            session,
            org_id,
            me,
            ago=timedelta(minutes=1),
            lat=MUMBAI[0],
            lng=MUMBAI[1],
            accuracy=Decimal("2000"),
        )
        await check_in(
            session,
            org_id,
            me,
            lat=NEAR_MUMBAI[0],
            lng=NEAR_MUMBAI[1],
            location_accuracy_m=Decimal("2000"),
        )
        assert await all_flags(session) == []

    async def test_a_punch_with_no_location_is_never_judged_for_travel(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_impossible_travel=True)
        await seed_signal(
            session, org_id, me, ago=timedelta(minutes=2), lat=MUMBAI[0], lng=MUMBAI[1]
        )
        await check_in(session, org_id, me)
        assert await all_flags(session) == []

    async def test_a_first_ever_located_punch_has_nothing_to_be_compared_with(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_impossible_travel=True)
        await check_in(session, org_id, me, lat=DELHI[0], lng=DELHI[1])
        assert await all_flags(session) == []

    async def test_an_unlocated_previous_punch_is_skipped_for_the_last_located_one(
        self, session, org_id, me
    ) -> None:
        """The most recent punch carried no fix; the one before it did. The comparison is against
        the last *located* punch, not against "no location, so nothing to say"."""
        await configure(session, org_id, detect_impossible_travel=True)
        await seed_signal(
            session, org_id, me, ago=timedelta(minutes=10), lat=MUMBAI[0], lng=MUMBAI[1]
        )
        await seed_signal(session, org_id, me, ago=timedelta(minutes=3))  # newer, no fix
        await check_in(session, org_id, me, lat=DELHI[0], lng=DELHI[1])
        assert [f.reason_code for f in await all_flags(session)] == ["impossible_travel"]

    async def test_a_check_out_is_compared_with_the_same_days_check_in(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_impossible_travel=True)
        await check_in(session, org_id, me, lat=MUMBAI[0], lng=MUMBAI[1])
        await check_out(session, org_id, me, lat=DELHI[0], lng=DELHI[1])
        (flag,) = await all_flags(session, "impossible_travel")
        assert flag.detail["from_kind"] == "check_in"

    async def test_another_employees_location_is_not_this_employees_previous_punch(
        self, session, org_id, me, other
    ) -> None:
        await configure(session, org_id, detect_impossible_travel=True)
        await seed_signal(
            session, org_id, other, ago=timedelta(minutes=2), lat=MUMBAI[0], lng=MUMBAI[1]
        )
        await check_in(session, org_id, me, lat=DELHI[0], lng=DELHI[1])
        assert await all_flags(session) == []

    async def test_another_tenants_history_for_the_same_employee_id_is_not_read(
        self, session, admin_session, org_id, me
    ) -> None:
        """Org B holds a Mumbai punch two minutes ago for the *same employee id*. Org A's punch
        from Delhi must not see it: the previous punch is looked up under the tenant."""
        await configure(session, org_id, detect_impossible_travel=True)
        other_org = uuid.uuid4()
        record_id = uuid.uuid4()
        when = datetime.now(UTC) - timedelta(minutes=2)
        async with admin_session.begin():
            await admin_session.execute(
                AttendanceRecord.__table__.insert().values(
                    id=record_id,
                    organization_id=other_org,
                    employee_id=me,
                    work_date=date(2020, 1, 1),
                    check_in_at=when,
                    source="web",
                    work_mode="office",
                    status="present",
                )
            )
            await admin_session.execute(
                PunchSignal.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    attendance_id=record_id,
                    employee_id=me,
                    kind="check_in",
                    punched_at=when,
                    lat=MUMBAI[0],
                    lng=MUMBAI[1],
                )
            )

        await check_in(session, org_id, me, lat=DELHI[0], lng=DELHI[1])

        assert await all_flags(session) == []


# --------------------------------------------------------------------- shared device


class TestSharedDevice:
    async def test_a_second_employee_on_the_same_device_is_flagged(
        self, session, org_id, me, other
    ) -> None:
        await configure(session, org_id, detect_shared_device=True)
        await check_in(session, org_id, other, device_id_hash=DEVICE_A)
        assert await all_flags(session) == []  # the first person to use a device is not flagged

        await check_in(session, org_id, me, device_id_hash=DEVICE_A)

        (flag,) = await all_flags(session, "shared_device")
        assert flag.employee_id == me
        assert flag.detail == {"other_employees": 1, "window_hours": 24}

    async def test_the_flag_names_a_count_never_the_colleague_or_the_device(
        self, session, org_id, me, other
    ) -> None:
        """A manager reading this flag on their own report must not learn who else, outside their
        team, used the same device — and must not be handed the device id in a JSON blob."""
        await configure(session, org_id, detect_shared_device=True)
        await check_in(session, org_id, other, device_id_hash=DEVICE_A)
        await check_in(session, org_id, me, device_id_hash=DEVICE_A)

        (flag,) = await all_flags(session, "shared_device")
        blob = json.dumps(flag.detail)
        assert str(other) not in blob
        assert DEVICE_A not in blob

    async def test_one_person_reusing_their_own_device_is_not_sharing_it(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_shared_device=True)
        await check_in(session, org_id, me, device_id_hash=DEVICE_A)
        await check_out(session, org_id, me, device_id_hash=DEVICE_A)
        assert await all_flags(session) == []

    async def test_different_devices_are_not_shared(self, session, org_id, me, other) -> None:
        await configure(session, org_id, detect_shared_device=True)
        await check_in(session, org_id, other, device_id_hash=DEVICE_A)
        await check_in(session, org_id, me, device_id_hash=DEVICE_B)
        assert await all_flags(session) == []

    async def test_two_other_employees_are_counted_as_two(self, session, org_id, me, other) -> None:
        await configure(session, org_id, detect_shared_device=True)
        third = uuid.uuid4()
        await seed_signal(session, org_id, other, ago=timedelta(hours=1), device=DEVICE_A)
        await seed_signal(session, org_id, third, ago=timedelta(hours=2), device=DEVICE_A)
        # The same person twice still counts once.
        await seed_signal(session, org_id, third, ago=timedelta(hours=3), device=DEVICE_A)

        await check_in(session, org_id, me, device_id_hash=DEVICE_A)

        (flag,) = await all_flags(session, "shared_device")
        assert flag.detail["other_employees"] == 2

    async def test_the_window_is_the_organizations_own(self, session, org_id, me, other) -> None:
        """The other person used the device 48 hours ago: outside a 24 h window."""
        await seed_signal(session, org_id, other, ago=timedelta(hours=48), device=DEVICE_A)

        await configure(session, org_id, detect_shared_device=True, shared_device_window_hours=24)
        await check_in(session, org_id, me, device_id_hash=DEVICE_A)
        assert await all_flags(session) == []

    async def test_a_wider_window_reaches_back_further(self, session, org_id, me, other) -> None:
        await seed_signal(session, org_id, other, ago=timedelta(hours=48), device=DEVICE_A)
        await configure(session, org_id, detect_shared_device=True, shared_device_window_hours=72)
        await check_in(session, org_id, me, device_id_hash=DEVICE_A)
        assert [f.detail["window_hours"] for f in await all_flags(session)] == [72]

    async def test_a_punch_with_no_device_id_cannot_be_judged(
        self, session, org_id, me, other
    ) -> None:
        await configure(session, org_id, detect_shared_device=True)
        await seed_signal(session, org_id, other, ago=timedelta(hours=1), device=DEVICE_A)
        await check_in(session, org_id, me)
        assert await all_flags(session) == []

    async def test_another_tenants_use_of_the_same_device_is_not_counted(
        self, session, admin_session, org_id, me, other
    ) -> None:
        await configure(session, org_id, detect_shared_device=True)
        other_org = uuid.uuid4()
        record_id = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                AttendanceRecord.__table__.insert().values(
                    id=record_id,
                    organization_id=other_org,
                    employee_id=other,
                    work_date=date(2020, 1, 1),
                    check_in_at=datetime.now(UTC),
                    source="web",
                    work_mode="office",
                    status="present",
                )
            )
            await admin_session.execute(
                PunchSignal.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    attendance_id=record_id,
                    employee_id=other,
                    kind="check_in",
                    punched_at=datetime.now(UTC) - timedelta(minutes=5),
                    # The value org A itself would compute: only the tenant filter keeps it out.
                    device_index=signals.device_index_for(org_id, DEVICE_A),
                )
            )

        await check_in(session, org_id, me, device_id_hash=DEVICE_A)

        assert await all_flags(session) == []


# --------------------------------------------------------------------- IP outside the ranges


OFFICE = ["203.0.113.0/24"]


class TestIpOutsideAllowedRanges:
    async def test_an_office_punch_from_outside_every_range_is_flagged(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=OFFICE)
        await check_in(session, org_id, me, request=make_request(xff="198.51.100.7"))
        (flag,) = await all_flags(session, "ip_outside_allowed")
        assert flag.detail == {"allowed_ranges": 1}

    async def test_an_office_punch_from_inside_a_range_is_not(self, session, org_id, me) -> None:
        await configure(session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=OFFICE)
        await check_in(session, org_id, me, request=make_request(xff="203.0.113.77"))
        assert await all_flags(session) == []

    async def test_the_first_forwarded_hop_is_the_client(self, session, org_id, me) -> None:
        """The same source the audit trail uses for its own address column."""
        await configure(session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=OFFICE)
        await check_in(
            session, org_id, me, request=make_request(xff="203.0.113.77, 198.51.100.7, 10.0.0.1")
        )
        assert await all_flags(session) == []

    async def test_with_no_forwarded_header_the_socket_peer_is_used(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=OFFICE)
        await check_in(session, org_id, me, request=make_request(client="203.0.113.5"))
        assert await all_flags(session) == []

    async def test_an_ipv4_client_on_a_dual_stack_socket_is_still_inside(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=OFFICE)
        await check_in(session, org_id, me, request=make_request(xff="::ffff:203.0.113.9"))
        assert await all_flags(session) == []

    async def test_an_ipv6_range_is_honoured(self, session, org_id, me, other) -> None:
        await configure(
            session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=["2001:db8:10::/48"]
        )
        await check_in(session, org_id, me, request=make_request(xff="2001:db8:10::7"))
        await check_in(session, org_id, other, request=make_request(xff="2001:db8:99::7"))
        assert [f.employee_id for f in await all_flags(session)] == [other]

    @pytest.mark.parametrize("work_mode", ["remote", "field", "hybrid"])
    async def test_only_office_punches_are_held_to_the_office_network(
        self, session, org_id, me, work_mode: str
    ) -> None:
        """A remote or field worker is, by definition, not on the office network."""
        await configure(session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=OFFICE)
        await check_in(
            session, org_id, me, work_mode=work_mode, request=make_request(xff="198.51.100.7")
        )
        assert await all_flags(session) == []

    @pytest.mark.parametrize("header", ["unknown", "", "not-an-ip", "203.0.113.9:443"])
    async def test_an_address_that_is_not_one_cannot_be_judged(
        self, session, org_id, me, header: str
    ) -> None:
        await configure(session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=OFFICE)
        await check_in(session, org_id, me, request=make_request(xff=header, client="not-an-ip"))
        assert await all_flags(session) == []

    async def test_no_request_means_no_address_and_no_flag(self, session, org_id, me) -> None:
        await configure(session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=OFFICE)
        await check_in(session, org_id, me)
        assert await all_flags(session) == []

    async def test_an_empty_range_list_flags_nothing_rather_than_everyone(
        self, session, org_id, me
    ) -> None:
        """The API refuses this configuration, but a row could still end up with the rule on and no
        ranges (a hand edit): the rule then has nothing to judge against and does not fire."""
        async with session.begin():
            session.add(
                SignalSettings(
                    organization_id=org_id,
                    detect_ip_outside_allowed=True,
                    detect_mock_location=False,
                    detect_impossible_travel=False,
                    detect_shared_device=False,
                    max_travel_speed_kmh=900,
                    shared_device_window_hours=24,
                    allowed_cidrs=[],
                )
            )
        await check_in(session, org_id, me, request=make_request(xff="198.51.100.7"))
        assert await all_flags(session) == []


# --------------------------------------------------------------------- personal data


class TestPersonalDataIsSealed:
    async def _one_signal(
        self, session: AsyncSession, org_id: uuid.UUID, me: uuid.UUID
    ) -> PunchSignal:
        await configure(session, org_id, detect_mock_location=True)
        await check_in(
            session,
            org_id,
            me,
            request=make_request(xff="203.0.113.77", ua="Mozilla/5.0 (X11; Linux) Firefox/128.0"),
            device_id_hash=DEVICE_A,
        )
        (signal,) = await all_signals(session)
        return signal

    async def test_the_address_and_device_id_are_ciphertext_with_a_mask_beside_them(
        self, session, org_id, me
    ) -> None:
        signal = await self._one_signal(session, org_id, me)

        assert isinstance(signal.ip_enc, bytes)
        assert isinstance(signal.device_id_enc, bytes)
        assert b"203.0.113.77" not in signal.ip_enc
        assert DEVICE_A.encode() not in signal.device_id_enc
        assert signal.ip_masked is not None and "203.0.113.77" not in signal.ip_masked
        assert signal.device_id_masked is not None and DEVICE_A not in signal.device_id_masked
        assert signal.ip_masked.endswith("77")  # the platform's generic mask: the last two only

    async def test_no_plaintext_of_either_value_is_recoverable_from_the_row(
        self, session, org_id, me
    ) -> None:
        """Every column of the row, dumped as text, holds neither the address nor the device id —
        the blind index is a keyed digest, not the value."""
        signal = await self._one_signal(session, org_id, me)
        async with session.begin():
            dump = (
                await session.execute(
                    text(
                        "SELECT row_to_json(p)::text FROM attendance.punch_signals p WHERE id = :id"
                    ),
                    {"id": signal.id},
                )
            ).scalar_one()
        assert "203.0.113.77" not in dump
        assert DEVICE_A not in dump
        assert signal.device_index is not None
        assert signal.device_index != DEVICE_A
        assert len(signal.device_index) == 64

    async def test_the_user_agent_is_kept_as_a_class_never_as_the_string(
        self, session, org_id, me
    ) -> None:
        signal = await self._one_signal(session, org_id, me)
        assert signal.user_agent_class == "browser"
        async with session.begin():
            dump = (
                await session.execute(
                    text(
                        "SELECT row_to_json(p)::text FROM attendance.punch_signals p WHERE id = :id"
                    ),
                    {"id": signal.id},
                )
            ).scalar_one()
        assert "Firefox" not in dump

    async def test_the_sealed_values_open_only_with_their_own_row_and_field(
        self, session, org_id, me
    ) -> None:
        from app.config import settings

        signal = await self._one_signal(session, org_id, me)
        assert signal.ip_enc is not None and signal.device_id_enc is not None

        opened = open_sealed(
            signal.ip_enc,
            settings=settings(),
            aad=signals.seal_aad(org_id, signal.id, "ip"),
        )
        assert opened == "203.0.113.77"

        # The same ciphertext presented as the other field, for another row, or another tenant.
        for aad in (
            signals.seal_aad(org_id, signal.id, "device_id"),
            signals.seal_aad(org_id, uuid.uuid4(), "ip"),
            signals.seal_aad(uuid.uuid4(), signal.id, "ip"),
        ):
            with pytest.raises(PiiError):
                open_sealed(signal.ip_enc, settings=settings(), aad=aad)

    async def test_two_punches_from_one_address_seal_to_different_ciphertext(
        self, session, org_id, me, other
    ) -> None:
        """A fresh nonce per seal: no equality oracle across rows on the sealed column."""
        await configure(session, org_id, detect_mock_location=True)
        for who in (me, other):
            await check_in(session, org_id, who, request=make_request(xff="203.0.113.77"))
        first, second = await all_signals(session)
        assert first.ip_enc != second.ip_enc

    async def test_a_punch_carries_none_of_it_back_to_the_caller(self, client, org_id, me) -> None:
        """The response to a punch is the record. It never mentions signals, flags or rules — the
        employee is not shown what was judged."""
        principal = principal_for(org_id, me, ["hrms.time.attendance.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/attendance/check-in",
                json={
                    "work_mode": "office",
                    "source": "web",
                    "mock_location": True,
                    "device_id_hash": DEVICE_A,
                    "location_accuracy_m": "12.5",
                },
                headers={"X-Forwarded-For": "198.51.100.7", "User-Agent": "curl/8.4.0"},
            )
        assert response.status_code == 201
        body = response.text
        for leaked in ("flag", "signal", "198.51.100.7", DEVICE_A, "mock_location", "curl"):
            assert leaked not in body


# --------------------------------------------------------------------- what is recorded


class TestWhatIsRecorded:
    async def test_the_punch_is_recorded_once_per_kind_with_the_servers_time(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_mock_location=True)
        before = datetime.now(UTC)
        await check_in(
            session, org_id, me, lat=MUMBAI[0], lng=MUMBAI[1], location_accuracy_m=Decimal("18.25")
        )
        await check_out(session, org_id, me, lat=NEAR_MUMBAI[0], lng=NEAR_MUMBAI[1])

        rows = sorted(await all_signals(session), key=lambda s: s.kind)
        assert [r.kind for r in rows] == ["check_in", "check_out"]
        assert rows[0].accuracy_m == Decimal("18.25")
        assert rows[0].lat == MUMBAI[0]
        assert all(before <= r.punched_at <= datetime.now(UTC) for r in rows)
        assert all(r.employee_id == me for r in rows)

    async def test_one_signal_per_punch_is_enforced_by_the_database(
        self, session, org_id, me
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        await configure(session, org_id, detect_mock_location=True)
        record = await check_in(session, org_id, me)
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    PunchSignal(
                        organization_id=org_id,
                        attendance_id=record.id,
                        employee_id=me,
                        kind="check_in",
                        punched_at=datetime.now(UTC),
                    )
                )

    async def test_one_flag_per_reason_per_signal_is_enforced_by_the_database(
        self, session, org_id, me
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, mock_location=True)
        (flag,) = await all_flags(session)
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    AttendanceFlag(
                        organization_id=org_id,
                        attendance_id=flag.attendance_id,
                        signal_id=flag.signal_id,
                        employee_id=me,
                        reason_code="mock_location",
                        detail={},
                    )
                )

    async def test_an_unknown_reason_code_is_refused_by_the_database(
        self, session, org_id, me
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, mock_location=True)
        (flag,) = await all_flags(session)
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    AttendanceFlag(
                        organization_id=org_id,
                        attendance_id=flag.attendance_id,
                        signal_id=flag.signal_id,
                        employee_id=me,
                        reason_code="vibes",
                        detail={},
                    )
                )

    async def test_several_rules_can_fire_on_one_punch(self, session, org_id, me, other) -> None:
        await configure(
            session,
            org_id,
            detect_mock_location=True,
            detect_shared_device=True,
            detect_ip_outside_allowed=True,
            allowed_cidrs=OFFICE,
        )
        await seed_signal(session, org_id, other, ago=timedelta(hours=1), device=DEVICE_A)
        await check_in(
            session,
            org_id,
            me,
            request=make_request(xff="198.51.100.7"),
            mock_location=True,
            device_id_hash=DEVICE_A,
        )
        assert sorted(f.reason_code for f in await all_flags(session)) == [
            "ip_outside_allowed",
            "mock_location",
            "shared_device",
        ]

    async def test_a_flagged_punch_writes_an_audit_row_with_reasons_and_nothing_personal(
        self, session, org_id, me
    ) -> None:
        await configure(
            session,
            org_id,
            detect_mock_location=True,
            detect_ip_outside_allowed=True,
            allowed_cidrs=OFFICE,
        )
        await check_in(
            session,
            org_id,
            me,
            request=make_request(xff="198.51.100.7"),
            mock_location=True,
            device_id_hash=DEVICE_A,
        )
        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "attendance.flagged")
                )
            ).scalar_one()
        assert row.after == {"kind": "check_in", "reasons": ["ip_outside_allowed", "mock_location"]}
        assert row.actor_id is not None
        assert DEVICE_A not in json.dumps(row.after)

    async def test_an_unflagged_punch_writes_no_flag_audit_row(self, session, org_id, me) -> None:
        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, mock_location=False)
        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "attendance.flagged")
                )
            ).scalar_one()
        assert count == 0


# --------------------------------------------------------------------- another tenant


class TestAnotherTenant:
    async def test_signals_flags_and_settings_of_another_tenant_are_invisible(
        self, session, admin_session, org_id, me
    ) -> None:
        other_org = uuid.uuid4()
        record_id, signal_id = uuid.uuid4(), uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                AttendanceRecord.__table__.insert().values(
                    id=record_id,
                    organization_id=other_org,
                    employee_id=me,
                    work_date=date(2020, 1, 1),
                    check_in_at=datetime.now(UTC),
                    source="web",
                    work_mode="office",
                    status="present",
                )
            )
            await admin_session.execute(
                PunchSignal.__table__.insert().values(
                    id=signal_id,
                    organization_id=other_org,
                    attendance_id=record_id,
                    employee_id=me,
                    kind="check_in",
                    punched_at=datetime.now(UTC),
                )
            )
            await admin_session.execute(
                AttendanceFlag.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    attendance_id=record_id,
                    signal_id=signal_id,
                    employee_id=me,
                    reason_code="mock_location",
                    detail={},
                )
            )
            await admin_session.execute(
                SignalSettings.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    detect_mock_location=True,
                    detect_impossible_travel=False,
                    detect_shared_device=False,
                    detect_ip_outside_allowed=False,
                    max_travel_speed_kmh=900,
                    shared_device_window_hours=24,
                    allowed_cidrs=[],
                )
            )

        async with session.begin():
            counts = [
                (await session.execute(select(func.count()).select_from(model))).scalar_one()
                for model in (PunchSignal, AttendanceFlag, SignalSettings)
            ]
        assert counts == [0, 0, 0]

    async def test_this_tenants_rules_do_not_apply_to_another_tenants_punches(
        self, session, admin_session, org_id, me
    ) -> None:
        """Org A turns mock-location detection on. A punch in org B (rules never configured) is
        untouched: the settings lookup is per tenant."""
        await configure(session, org_id, detect_mock_location=True)
        other_org = uuid.uuid4()
        ctx = ctx_for(other_org, me, "hrms.time.attendance.write", Scope.OWN)
        from sqlalchemy import event

        from platform_core.db import TENANT_GUC

        @event.listens_for(session.sync_session, "after_begin")
        def _rebind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{other_org}', true)")

        try:
            async with session.begin():
                await services.check_in(
                    session, ctx, CheckInIn(work_mode="office", source="web", mock_location=True)
                )
            async with session.begin():
                flags = (
                    await session.execute(select(func.count()).select_from(AttendanceFlag))
                ).scalar_one()
        finally:
            event.remove(session.sync_session, "after_begin", _rebind)
        assert flags == 0

    async def test_the_app_role_cannot_write_a_signal_for_another_tenant(
        self, session, org_id, me
    ) -> None:
        from sqlalchemy.exc import DBAPIError

        record = await check_in(session, org_id, me)
        with pytest.raises(DBAPIError):
            async with session.begin():
                session.add(
                    PunchSignal(
                        organization_id=uuid.uuid4(),
                        attendance_id=record.id,
                        employee_id=me,
                        kind="check_out",
                        punched_at=datetime.now(UTC),
                    )
                )
