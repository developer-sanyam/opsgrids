"""R22 review round: the promises the signal stack makes about *the punch*.

Observe-only means a defect in the rule engine, in sealing or in storage can never refuse, delay or
half-write an attendance record — proved here by injecting a failure at every stage and asserting
the punch is exactly what it would have been. A normal-length device id must not be the thing that
makes it fail (a 64-character SHA-256 hex digest used to overflow its mask column). An IPv6 zone id
must not turn an audited request into a 500. And the shared-device index must not link one person's
employers, so it is keyed by the organization.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services, signals
from app.domain import parse_ip
from app.main import app
from app.models import (
    AttendanceFlag,
    AttendanceRecord,
    AuditLog,
    PunchSignal,
)
from app.schemas import CheckInIn
from platform_core.auth import Principal, Scope, get_principal
from platform_core.pii import blind_index
from tests.helpers import ctx_for, principal_for
from tests.signal_helpers import (
    DELHI,
    DEVICE_A,
    MUMBAI,
    all_flags,
    all_signals,
    check_in,
    check_out,
    configure,
    make_request,
    seed_signal,
)

pytestmark = pytest.mark.integration

#: A SHA-256 hex digest — what a client that hashes its device id sends — and the schema's ceiling.
SHA256_ID = (
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"  # pragma: allowlist secret
)
MAX_ID = "d" * 128
LONGEST_V6 = "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


class _Recorder:
    """Stands in for `signals.log`: keeps every call so a test can read exactly what was logged."""

    def __init__(self) -> None:
        self.errors: list[tuple[str, dict[str, Any]]] = []

    def error(self, event: str, **fields: Any) -> None:
        self.errors.append((event, fields))

    def info(self, *_: Any, **__: Any) -> None:
        return None


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(signals, "log", recorder)
    return recorder


@pytest.fixture
def me() -> uuid.UUID:
    return uuid.uuid4()


# --------------------------------------------------------------------- long device ids


class TestOrdinaryDeviceIdsFitTheirColumns:
    @pytest.mark.parametrize("device_id", [SHA256_ID, MAX_ID], ids=["sha256-64", "schema-max-128"])
    async def test_a_check_in_with_a_normal_length_id_stores_its_signal(
        self, session, org_id, me, device_id: str
    ) -> None:
        """The generic mask keeps the input's length, so a 64-character digest masks to 64. The
        column was 40 wide: the insert failed and — before the punch was insulated from it — took
        the whole check-in with it."""
        await configure(session, org_id, detect_mock_location=True)

        record = await check_in(session, org_id, me, device_id_hash=device_id, mock_location=True)

        assert record.status == "present"
        (signal,) = await all_signals(session)
        assert signal.device_id_masked is not None
        assert len(signal.device_id_masked) == len(device_id)
        assert device_id not in signal.device_id_masked
        assert len(await all_flags(session)) == 1

    @pytest.mark.parametrize("device_id", [SHA256_ID, MAX_ID], ids=["sha256-64", "schema-max-128"])
    async def test_a_check_out_with_a_normal_length_id_stores_its_signal_too(
        self, session, org_id, me, device_id: str
    ) -> None:
        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, device_id_hash=device_id)

        record = await check_out(session, org_id, me, device_id_hash=device_id)

        assert record.check_out_at is not None
        signals_ = sorted(await all_signals(session), key=lambda s: s.kind)
        assert [s.kind for s in signals_] == ["check_in", "check_out"]
        assert all(
            s.device_id_masked and len(s.device_id_masked) == len(device_id) for s in signals_
        )

    async def test_the_same_through_the_http_route(
        self, client: AsyncClient, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_mock_location=True)
        principal = principal_for(org_id, me, ["hrms.time.attendance.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/attendance/check-in",
                json={"work_mode": "office", "source": "web", "device_id_hash": SHA256_ID},
            )
        assert response.status_code == 201
        (signal,) = await all_signals(session)
        assert signal.device_id_enc is not None

    async def test_the_longest_ipv6_address_fits_its_mask(self, session, org_id, me) -> None:
        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, request=make_request(xff=LONGEST_V6))
        (signal,) = await all_signals(session)
        assert signal.ip_masked is not None and len(signal.ip_masked) == len(LONGEST_V6)


# --------------------------------------------------------------------- observe-only holds


async def _boom(*_: Any, **__: Any) -> Any:
    raise RuntimeError("injected failure carrying 203.0.113.77 and " + DEVICE_A)


def _boom_sync(*_: Any, **__: Any) -> Any:
    raise RuntimeError("injected failure carrying 203.0.113.77 and " + DEVICE_A)


def _bad_kind_signal(**kw: Any) -> PunchSignal:
    """A signal the database refuses (its CHECK constraint on `kind`) — a storage failure."""
    kw["kind"] = "not-a-kind"
    return PunchSignal(**kw)


def _bad_reason_flag(**kw: Any) -> AttendanceFlag:
    """A flag the database refuses — a storage failure *after* the signal was inserted."""
    kw["reason_code"] = "vibes"
    return AttendanceFlag(**kw)


ALL_RULES = {
    "detect_mock_location": True,
    "detect_impossible_travel": True,
    "detect_shared_device": True,
    "detect_ip_outside_allowed": True,
    "allowed_cidrs": ["203.0.113.0/24"],
}

#: (name, what to break) — every stage of `record_punch`, from the settings read to the audit row.
STAGES = [
    ("the settings read", lambda mp: mp.setattr(signals.selectors, "get_signal_settings", _boom)),
    ("the address parse", lambda mp: mp.setattr(signals, "parse_ip", _boom_sync)),
    ("the user-agent class", lambda mp: mp.setattr(signals, "classify_user_agent", _boom_sync)),
    (
        "the previous-punch read",
        lambda mp: mp.setattr(signals.selectors, "get_previous_located_signal", _boom),
    ),
    ("the travel rule", lambda mp: mp.setattr(signals, "assess_travel", _boom_sync)),
    (
        "the shared-device read",
        lambda mp: mp.setattr(signals.selectors, "count_other_device_users", _boom),
    ),
    ("the range rule", lambda mp: mp.setattr(signals, "ip_in_any", _boom_sync)),
    ("sealing", lambda mp: mp.setattr(signals, "seal", _boom_sync)),
    ("the blind index", lambda mp: mp.setattr(signals, "device_index_for", _boom_sync)),
    ("the signal insert", lambda mp: mp.setattr(signals, "PunchSignal", _bad_kind_signal)),
    ("the flag insert", lambda mp: mp.setattr(signals, "AttendanceFlag", _bad_reason_flag)),
    ("the audit row", lambda mp: mp.setattr(signals, "record_audit", _boom_sync)),
]


@pytest.fixture
async def rich_history(session: AsyncSession, org_id: uuid.UUID, me: uuid.UUID) -> uuid.UUID:
    """Every rule on, and history that reaches every one of them: a previous located punch for
    `me` and another employee on the same device, so each stage above is actually exercised."""
    other = uuid.uuid4()
    await configure(session, org_id, **ALL_RULES)
    await seed_signal(session, org_id, me, ago=timedelta(minutes=2), lat=MUMBAI[0], lng=MUMBAI[1])
    await seed_signal(session, org_id, other, ago=timedelta(hours=1), device=DEVICE_A)
    return other


class TestAFailureNeverFailsThePunch:
    @pytest.mark.parametrize(("stage", "inject"), STAGES, ids=[s for s, _ in STAGES])
    async def test_a_failure_at_each_stage_leaves_the_check_in_exactly_as_it_would_be(
        self, session, org_id, me, rich_history, log, monkeypatch, stage: str, inject
    ) -> None:
        inject(monkeypatch)

        record = await check_in(
            session,
            org_id,
            me,
            request=make_request(xff="203.0.113.77", ua="curl/8.4.0"),
            lat=DELHI[0],
            lng=DELHI[1],
            mock_location=True,
            device_id_hash=DEVICE_A,
        )

        # The punch: recorded, present, attributed, with its own audit row and event.
        assert record.status == "present"
        assert record.employee_id == me
        async with session.begin():
            stored = (
                await session.execute(
                    select(AttendanceRecord).where(AttendanceRecord.id == record.id)
                )
            ).scalar_one()
            audits = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "attendance.checked_in")
                )
            ).scalar_one()
        assert stored.check_in_lat == DELHI[0]
        assert audits == 1
        # Nothing half-written: no signal for this punch and no flag, whichever stage broke.
        mine = [s for s in await all_signals(session) if s.attendance_id == record.id]
        assert mine == []
        assert await all_flags(session) == []
        # Logged once, at error level, by code.
        assert [event for event, _ in log.errors] == ["attendance.signals.failed"]

    @pytest.mark.parametrize(("stage", "inject"), STAGES, ids=[s for s, _ in STAGES])
    async def test_a_failure_at_each_stage_leaves_the_check_out_exactly_as_it_would_be(
        self, session, org_id, me, rich_history, log, monkeypatch, stage: str, inject
    ) -> None:
        # The check-in goes through cleanly, so the check-out has a real day to close.
        await check_in(session, org_id, me)
        log.errors.clear()
        inject(monkeypatch)

        record = await check_out(
            session,
            org_id,
            me,
            request=make_request(xff="203.0.113.77"),
            lat=DELHI[0],
            lng=DELHI[1],
            mock_location=True,
            device_id_hash=DEVICE_A,
        )

        assert record.check_out_at is not None
        async with session.begin():
            stored = (
                await session.execute(
                    select(AttendanceRecord).where(AttendanceRecord.id == record.id)
                )
            ).scalar_one()
        # The close itself — time and fix — was persisted, not rolled back with the signal.
        assert stored.check_out_at is not None
        assert stored.check_out_lat == DELHI[0]
        # Only the (clean) check-in's signal exists for this day: the check-out's was not stored.
        kinds = [s.kind for s in await all_signals(session) if s.attendance_id == record.id]
        assert kinds == ["check_in"]
        assert await all_flags(session) == []
        assert [event for event, _ in log.errors] == ["attendance.signals.failed"]

    async def test_the_log_carries_codes_and_never_the_values(
        self, session, org_id, me, rich_history, log, monkeypatch
    ) -> None:
        """The injected error's *message* holds an address and a device id, as a driver error can
        hold a statement's parameters. Only the class and SQLSTATE may be logged."""
        monkeypatch.setattr(signals, "seal", _boom_sync)

        await check_in(
            session,
            org_id,
            me,
            request=make_request(xff="203.0.113.77"),
            device_id_hash=DEVICE_A,
        )

        ((event, fields),) = log.errors
        assert event == "attendance.signals.failed"
        assert fields["error_type"] == "RuntimeError"
        assert fields["kind"] == "check_in"
        assert fields["organization_id"] == str(org_id)
        assert "203.0.113.77" not in repr(fields)
        assert DEVICE_A not in repr(fields)

    async def test_a_database_failure_logs_its_sqlstate(
        self, session, org_id, me, rich_history, log, monkeypatch
    ) -> None:
        monkeypatch.setattr(signals, "PunchSignal", _bad_kind_signal)
        await check_in(session, org_id, me)
        ((_, fields),) = log.errors
        assert fields["sqlstate"] == "23514"  # check_violation
        assert fields["error_type"] == "IntegrityError"

    async def test_a_flag_that_cannot_be_stored_takes_its_signal_with_it(
        self, session, org_id, me, rich_history, log, monkeypatch
    ) -> None:
        """Half a write is the failure to fear: the signal inserted, the flag refused. The savepoint
        rolls both back together, so evidence never exists without the judgement about it."""
        monkeypatch.setattr(signals, "AttendanceFlag", _bad_reason_flag)

        record = await check_in(session, org_id, me, mock_location=True)

        assert [s for s in await all_signals(session) if s.attendance_id == record.id] == []

    async def test_the_transaction_is_still_usable_afterwards(
        self, session, org_id, me, rich_history, log, monkeypatch
    ) -> None:
        """A database error inside the savepoint must not poison the outer transaction: the
        punch's own audit row and event were written *after* the failure and must commit."""
        monkeypatch.setattr(signals, "PunchSignal", _bad_kind_signal)
        await check_in(session, org_id, me)
        async with session.begin():
            events = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "attendance.checked_in")
                )
            ).scalar_one()
        assert events == 1

    async def test_with_the_fault_gone_the_next_punch_is_recorded_normally(
        self, session, org_id, me, rich_history, log, monkeypatch
    ) -> None:
        """A failure is not sticky: nothing about one broken punch changes the next."""
        with monkeypatch.context() as broken:
            broken.setattr(signals, "seal", _boom_sync)
            # `seal` only runs when there is something to seal — an address or a device id.
            await check_in(session, org_id, me, mock_location=True, device_id_hash=DEVICE_A)
        assert log.errors

        await check_out(session, org_id, me, mock_location=True)

        assert [f.reason_code for f in await all_flags(session)] == ["mock_location"]

    async def test_cancellation_is_not_swallowed(
        self, session, org_id, me, rich_history, monkeypatch
    ) -> None:
        """Only `Exception` is caught: a cancelled request must still be cancelled."""
        import asyncio

        async def _cancel(*_: Any, **__: Any) -> Any:
            raise asyncio.CancelledError

        monkeypatch.setattr(signals.selectors, "get_signal_settings", _cancel)
        ctx = ctx_for(org_id, me, "hrms.time.attendance.write", Scope.OWN)
        with pytest.raises(asyncio.CancelledError):
            async with session.begin():
                await services.check_in(session, ctx, CheckInIn(work_mode="office", source="web"))

    async def test_a_failure_with_the_rules_off_is_impossible_to_reach(
        self, session, org_id, me, log, monkeypatch
    ) -> None:
        """No settings, no work: the injected fault is never touched and nothing is logged."""
        monkeypatch.setattr(signals, "seal", _boom_sync)
        await check_in(session, org_id, me, device_id_hash=DEVICE_A)
        assert log.errors == []


# --------------------------------------------------------------------- IPv6 zone ids


class TestAZoneIdIsNoClientAddress:
    @pytest.mark.parametrize(
        "raw",
        ["fe80::1%eth0", "fe80::1%1", "fe80::1%" + "z" * 200, "FE80::ABCD%en0"],
    )
    def test_it_is_not_an_address_at_all(self, raw: str) -> None:
        assert parse_ip(raw) is None

    def test_the_same_address_without_a_zone_is_unchanged(self) -> None:
        assert parse_ip("fe80::1") == "fe80::1"

    @pytest.mark.parametrize("header", ["fe80::1%eth0", "fe80::1%" + "z" * 200])
    async def test_a_zone_id_does_not_turn_a_punch_into_a_500(
        self, client: AsyncClient, session, org_id, me, header: str
    ) -> None:
        """PostgreSQL's INET refuses `fe80::1%eth0` while Python parses it, so the audit insert
        failed — with no rule on, so nothing here is about the signal stack."""
        principal = principal_for(org_id, me, ["hrms.time.attendance.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/attendance/check-in",
                json={"work_mode": "office", "source": "web"},
                headers={"X-Forwarded-For": header},
            )
        assert response.status_code == 201
        async with session.begin():
            ip = (
                await session.execute(
                    select(AuditLog.ip).where(AuditLog.action == "attendance.checked_in")
                )
            ).scalar_one()
        assert ip is None

    async def test_a_zone_id_is_never_stored_or_judged_with_the_rules_on(
        self, session, org_id, me
    ) -> None:
        await configure(
            session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=["203.0.113.0/24"]
        )
        await check_in(session, org_id, me, request=make_request(xff="fe80::1%" + "z" * 200))
        (signal,) = await all_signals(session)
        assert signal.ip_enc is None and signal.ip_masked is None
        assert await all_flags(session) == []


# --------------------------------------------------------------------- the blind index


class TestTheDeviceIndexIsPerOrganization:
    def test_one_device_yields_a_different_index_in_every_organization(self) -> None:
        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        assert signals.device_index_for(org_a, DEVICE_A) != signals.device_index_for(
            org_b, DEVICE_A
        )

    def test_within_one_organization_it_is_stable(self) -> None:
        org = uuid.uuid4()
        assert signals.device_index_for(org, DEVICE_A) == signals.device_index_for(org, DEVICE_A)

    def test_two_devices_in_one_organization_differ(self) -> None:
        org = uuid.uuid4()
        assert signals.device_index_for(org, DEVICE_A) != signals.device_index_for(org, "x" * 32)

    def test_it_is_not_the_organization_free_value_any_more(self) -> None:
        """The old index was the same in every tenant. No organization's index may equal it."""
        from app.config import settings

        legacy = blind_index(DEVICE_A, kind="attendance.device_id", settings=settings())
        assert signals.device_index_for(uuid.uuid4(), DEVICE_A) != legacy

    async def test_the_stored_index_is_the_organizations_own(self, session, org_id, me) -> None:
        await configure(session, org_id, detect_shared_device=True)
        await check_in(session, org_id, me, device_id_hash=DEVICE_A)
        (signal,) = await all_signals(session)
        assert signal.device_index == signals.device_index_for(org_id, DEVICE_A)
        assert signal.device_index != signals.device_index_for(uuid.uuid4(), DEVICE_A)

    async def test_the_shared_device_rule_still_works_within_an_organization(
        self, session, org_id, me
    ) -> None:
        await configure(session, org_id, detect_shared_device=True)
        await check_in(session, org_id, uuid.uuid4(), device_id_hash=DEVICE_A)

        await check_in(session, org_id, me, device_id_hash=DEVICE_A)

        (flag,) = await all_flags(session, "shared_device")
        assert flag.employee_id == me


# --------------------------------------------------------------------- the unmask response


class TestTheUnmaskResponseIsNotCached:
    async def test_it_carries_no_store(self, client: AsyncClient, session, org_id, me) -> None:
        await configure(session, org_id, detect_mock_location=True)
        await check_in(session, org_id, me, request=make_request(xff="203.0.113.10"))
        (signal,) = await all_signals(session)
        hr = principal_for(
            org_id, uuid.uuid4(), ["hrms.time.attendance.read_sensitive:ORG"], amr=["otp"]
        )
        with as_principal(hr):
            response = await client.post(
                f"/api/v1/attendance/signals/{signal.id}/unmask", json={"fields": ["ip"]}
            )
        assert response.status_code == 200
        assert response.json()["ip"] == "203.0.113.10"
        assert response.headers["cache-control"] == "no-store"


# --------------------------------------------------------------------- what the docs promise


class TestTheOfficeRuleStatesItsLimit:
    """The rule keys on the work mode the *client declares*, so a client that declares `remote`
    is not judged. That is a decision, not an oversight — and it is written where an administrator
    configuring the rule will read it."""

    SENTENCE = "the office-network rule applies only to punches declared as office"

    def test_the_settings_routes_say_so(self) -> None:
        paths = app.openapi()["paths"]["/api/v1/attendance/signal-settings"]
        for method in ("get", "put"):
            description = " ".join(paths[method]["description"].lower().split())
            assert self.SENTENCE in description

    def test_the_setting_itself_says_so(self) -> None:
        schema = app.openapi()["components"]["schemas"]["SignalSettingsIn"]
        description = schema["properties"]["detect_ip_outside_allowed"]["description"]
        assert self.SENTENCE in " ".join(description.lower().split())
