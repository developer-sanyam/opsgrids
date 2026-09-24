"""R22: who may see the anti-spoofing flags, reveal what is behind them, and change the rules.

Four routes, each with the negative half spelled out: no credential, no entitlement, the wrong
permission, the right permission at the wrong scope, the right permission without a second factor,
someone else's record, another tenant's record. The flags list is narrowed exactly as the attendance
records it describes are; the unmask route re-scopes to the one punch named and audits every read;
the settings routes are the org time-admin's and nobody else's.

The values under test are real: punches went through the real write path with rules on, so the
sealed columns hold real ciphertext and a wrong scope fails at the *authorization* check rather than
at "no such row".
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services, signals
from app.catalogue import HRMS, HRMS_ROLE_TEMPLATES
from app.main import app
from app.models import (
    AttendanceFlag,
    AttendanceRecord,
    AuditLog,
    PunchSignal,
    SignalSettings,
)
from platform_core.auth import Principal, Scope, get_principal
from platform_core.pii import PiiError
from tests.helpers import ctx_for, principal_for
from tests.signal_helpers import (
    DEVICE_A,
    DEVICE_B,
    all_flags,
    all_signals,
    check_in,
    configure,
    make_request,
    seed_signal,
)

pytestmark = pytest.mark.integration

FLAG_READ = "hrms.time.attendance_flag.read"
SENSITIVE = "hrms.time.attendance.read_sensitive"
SHIFT_WRITE = "hrms.time.shift.write"
SHIFT_READ = "hrms.time.shift.read"

REP_IP = "203.0.113.10"
STRANGER_IP = "198.51.100.20"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def hr_principal(org_id: uuid.UUID, employee: uuid.UUID | None = None, **kw: Any) -> Principal:
    return principal_for(
        org_id,
        employee or uuid.uuid4(),
        [f"{FLAG_READ}:ORG", f"{SENSITIVE}:ORG", f"{SHIFT_WRITE}:ORG"],
        amr=kw.pop("amr", ["otp"]),
        **kw,
    )


@pytest.fixture
async def world(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """`mgr` manages `rep`; `stranger` reports to nobody. Both punched in from different sites with
    a claimed mock location, so each has one flag, a sealed address and a sealed device id."""
    mgr, rep, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await configure(session, org_id, detect_mock_location=True)
    now = datetime.now(UTC)
    async with session.begin():
        await services.upsert_team_membership_bulk(
            session,
            organization_id=org_id,
            rows=[
                services.TeamMembershipWrite(mgr, None, "active", 1, now),
                services.TeamMembershipWrite(rep, mgr, "active", 1, now),
                services.TeamMembershipWrite(stranger, None, "active", 1, now),
            ],
        )
    rep_record = await check_in(
        session,
        org_id,
        rep,
        request=make_request(xff=REP_IP, ua="Mozilla/5.0 (X11; Linux) Firefox/128.0"),
        location_code="MUM",
        mock_location=True,
        device_id_hash=DEVICE_A,
    )
    stranger_record = await check_in(
        session,
        org_id,
        stranger,
        request=make_request(xff=STRANGER_IP, ua="okhttp/4.12.0"),
        location_code="DEL",
        mock_location=True,
        device_id_hash=DEVICE_B,
    )
    by_employee = {s.employee_id: s.id for s in await all_signals(session)}
    return {
        "mgr": mgr,
        "rep": rep,
        "stranger": stranger,
        "rep_record": rep_record.id,
        "stranger_record": stranger_record.id,
        "rep_signal": by_employee[rep],
        "stranger_signal": by_employee[stranger],
    }


async def _audit(session: AsyncSession, action: str) -> list[AuditLog]:
    async with session.begin():
        return list(
            (await session.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )


# --------------------------------------------------------------------- the catalogue


class TestTheCatalogue:
    def test_the_new_permissions_are_declared(self) -> None:
        codes = set(HRMS.codes())
        assert {FLAG_READ, SENSITIVE} <= codes

    def test_unmasking_is_dangerous_and_is_not_a_scope_of_reading(self) -> None:
        by_code = {s.code("hrms"): s for s in HRMS.specs}
        assert by_code[SENSITIVE].is_dangerous
        assert by_code[SENSITIVE].max_scope == "ORG"
        # The wildcard match keeps the dot, so no read grant can ever satisfy the unmask one.
        assert by_code["hrms.time.attendance.read"].is_dangerous is False

    def test_hr_holds_both_and_an_employee_holds_neither(self) -> None:
        templates = {t.code: {g.code: g.scope for g in t.grants} for t in HRMS_ROLE_TEMPLATES}
        assert templates["HR_ADMIN"][FLAG_READ] == "ORG"
        assert templates["HR_ADMIN"][SENSITIVE] == "ORG"
        assert FLAG_READ not in templates["EMPLOYEE"]
        assert SENSITIVE not in templates["EMPLOYEE"]


# --------------------------------------------------------------------- the flags list


class TestListingFlags:
    async def test_no_credential_is_401(self, client: AsyncClient, world) -> None:
        assert (await client.get("/api/v1/attendance/flags")).status_code == 401

    async def test_no_entitlement_is_402(self, client: AsyncClient, org_id, world) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [f"{FLAG_READ}:ORG"], entitlements=[])
        with as_principal(principal):
            assert (await client.get("/api/v1/attendance/flags")).status_code == 402

    async def test_holding_no_flag_permission_is_403(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.attendance.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/attendance/flags")
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_an_own_scoped_grant_is_refused_not_answered_with_ones_own_flags(
        self, client: AsyncClient, org_id, world
    ) -> None:
        """An employee is not shown the rules applied to them. The floor is TEAM."""
        principal = principal_for(org_id, world["rep"], [f"{FLAG_READ}:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/attendance/flags")
        assert response.status_code == 403
        assert "TEAM" in response.json()["detail"]

    async def test_hr_sees_every_flag_with_the_signals_masked(
        self, client: AsyncClient, org_id, world
    ) -> None:
        with as_principal(hr_principal(org_id)):
            response = await client.get("/api/v1/attendance/flags")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        by_employee = {i["employee_id"]: i for i in body["items"]}
        assert set(by_employee) == {str(world["rep"]), str(world["stranger"])}

        rep = by_employee[str(world["rep"])]
        assert rep["reason_code"] == "mock_location"
        assert rep["work_date"] == datetime.now(UTC).date().isoformat()
        assert rep["attendance_id"] == str(world["rep_record"])
        signal = rep["signal"]
        assert signal["id"] == str(world["rep_signal"])
        assert signal["kind"] == "check_in"
        assert signal["mock_location"] is True
        assert signal["user_agent_class"] == "browser"
        assert signal["ip_masked"] and signal["device_id_masked"]

    async def test_no_response_ever_carries_an_address_a_device_id_or_a_ciphertext(
        self, client: AsyncClient, org_id, world
    ) -> None:
        with as_principal(hr_principal(org_id)):
            text = (await client.get("/api/v1/attendance/flags")).text
        for leaked in (
            REP_IP,
            STRANGER_IP,
            DEVICE_A,
            DEVICE_B,
            "ip_enc",
            "device_id_enc",
            "device_index",
        ):
            assert leaked not in text

    async def test_a_manager_sees_only_their_reports_flags(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(org_id, world["mgr"], [f"{FLAG_READ}:TEAM"])
        with as_principal(principal):
            body = (await client.get("/api/v1/attendance/flags")).json()
        assert [i["employee_id"] for i in body["items"]] == [str(world["rep"])]
        # The total is the caller's own, not the organization's.
        assert body["total"] == 1

    async def test_a_manager_asking_for_someone_elses_employee_id_gets_nothing(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(org_id, world["mgr"], [f"{FLAG_READ}:TEAM"])
        with as_principal(principal):
            body = (
                await client.get(
                    "/api/v1/attendance/flags", params={"employee_id": str(world["stranger"])}
                )
            ).json()
        assert body["items"] == [] and body["total"] == 0

    async def test_a_team_caller_with_no_employee_id_sees_nothing(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(org_id, None, [f"{FLAG_READ}:TEAM"])
        with as_principal(principal):
            body = (await client.get("/api/v1/attendance/flags")).json()
        assert body["items"] == []

    async def test_an_exited_report_s_flags_leave_the_managers_view(
        self, client: AsyncClient, session, org_id, world
    ) -> None:
        async with session.begin():
            await services.upsert_team_membership_bulk(
                session,
                organization_id=org_id,
                rows=[
                    services.TeamMembershipWrite(
                        world["rep"], world["mgr"], "exited", 2, datetime.now(UTC)
                    )
                ],
            )
        principal = principal_for(org_id, world["mgr"], [f"{FLAG_READ}:TEAM"])
        with as_principal(principal):
            body = (await client.get("/api/v1/attendance/flags")).json()
        assert body["items"] == []

    async def test_a_location_scoped_caller_sees_only_their_sites_flags(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(
            org_id, uuid.uuid4(), [f"{FLAG_READ}:LOCATION"], locations=["DEL"]
        )
        with as_principal(principal):
            body = (await client.get("/api/v1/attendance/flags")).json()
        assert [i["employee_id"] for i in body["items"]] == [str(world["stranger"])]

    async def test_a_location_scoped_caller_with_no_locations_sees_nothing(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [f"{FLAG_READ}:LOCATION"], locations=[])
        with as_principal(principal):
            body = (await client.get("/api/v1/attendance/flags")).json()
        assert body["items"] == []

    async def test_filters_narrow_within_the_scope(
        self, client: AsyncClient, org_id, world
    ) -> None:
        with as_principal(hr_principal(org_id)):
            by_employee = (
                await client.get(
                    "/api/v1/attendance/flags", params={"employee_id": str(world["rep"])}
                )
            ).json()
            by_reason = (
                await client.get("/api/v1/attendance/flags", params={"reason": "shared_device"})
            ).json()
            today = datetime.now(UTC).date()
            future = (
                await client.get(
                    "/api/v1/attendance/flags", params={"date_from": str(today + timedelta(days=1))}
                )
            ).json()
            past = (
                await client.get(
                    "/api/v1/attendance/flags", params={"date_to": str(today - timedelta(days=1))}
                )
            ).json()
            todays = (
                await client.get(
                    "/api/v1/attendance/flags",
                    params={"date_from": str(today), "date_to": str(today)},
                )
            ).json()
        assert [i["employee_id"] for i in by_employee["items"]] == [str(world["rep"])]
        assert by_reason["items"] == []
        assert future["items"] == [] and past["items"] == []
        assert todays["total"] == 2

    async def test_an_unknown_reason_is_a_422(self, client: AsyncClient, org_id, world) -> None:
        with as_principal(hr_principal(org_id)):
            response = await client.get("/api/v1/attendance/flags", params={"reason": "vibes"})
        assert response.status_code == 422

    async def test_the_list_is_paginated_and_bounded(
        self, client: AsyncClient, org_id, world
    ) -> None:
        with as_principal(hr_principal(org_id)):
            first = (await client.get("/api/v1/attendance/flags", params={"size": 1})).json()
            second = (
                await client.get("/api/v1/attendance/flags", params={"size": 1, "page": 2})
            ).json()
            beyond = (
                await client.get("/api/v1/attendance/flags", params={"size": 1, "page": 3})
            ).json()
            too_big = await client.get("/api/v1/attendance/flags", params={"size": 201})
        assert first["total"] == second["total"] == 2
        assert {first["items"][0]["id"], second["items"][0]["id"]} != set()
        assert first["items"][0]["id"] != second["items"][0]["id"]
        assert beyond["items"] == []
        assert too_big.status_code == 422

    async def test_another_tenants_flags_are_invisible(
        self, client: AsyncClient, session, admin_session, org_id, world
    ) -> None:
        other_org = uuid.uuid4()
        record_id, signal_id = uuid.uuid4(), uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                AttendanceRecord.__table__.insert().values(
                    id=record_id,
                    organization_id=other_org,
                    employee_id=world["rep"],
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
                    employee_id=world["rep"],
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
                    employee_id=world["rep"],
                    reason_code="impossible_travel",
                    detail={"speed_kmh": 99999},
                )
            )
        with as_principal(hr_principal(org_id)):
            body = (await client.get("/api/v1/attendance/flags")).json()
        assert body["total"] == 2
        assert "impossible_travel" not in str(body)


# --------------------------------------------------------------------- unmasking


class TestUnmasking:
    URL = "/api/v1/attendance/signals/{}/unmask"
    BOTH: ClassVar[dict[str, Any]] = {"fields": ["ip", "device_id"]}

    async def _post(
        self, client: AsyncClient, principal: Principal, signal_id: Any, body: Any
    ) -> Any:
        with as_principal(principal):
            return await client.post(self.URL.format(signal_id), json=body)

    async def test_no_credential_is_401(self, client: AsyncClient, world) -> None:
        response = await client.post(self.URL.format(world["rep_signal"]), json=self.BOTH)
        assert response.status_code == 401

    async def test_no_entitlement_is_402(self, client: AsyncClient, org_id, world) -> None:
        principal = principal_for(
            org_id, uuid.uuid4(), [f"{SENSITIVE}:ORG"], amr=["otp"], entitlements=[]
        )
        response = await self._post(client, principal, world["rep_signal"], self.BOTH)
        assert response.status_code == 402

    async def test_reading_flags_is_not_permission_to_unmask(
        self, client: AsyncClient, session, org_id, world
    ) -> None:
        principal = principal_for(
            org_id,
            uuid.uuid4(),
            [f"{FLAG_READ}:ORG", "hrms.time.attendance.read:ORG"],
            amr=["otp"],
        )
        response = await self._post(client, principal, world["rep_signal"], self.BOTH)
        assert response.status_code == 403
        assert await _audit(session, "entity.sensitive_read") == []

    async def test_the_permission_without_a_second_factor_is_refused(
        self, client: AsyncClient, session, org_id, world
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [f"{SENSITIVE}:ORG"], amr=[])
        response = await self._post(client, principal, world["rep_signal"], self.BOTH)
        assert response.status_code == 403
        assert "otp" in response.json()["detail"]
        assert await _audit(session, "entity.sensitive_read") == []

    async def test_hr_with_a_second_factor_reads_both_values(
        self, client: AsyncClient, org_id, world
    ) -> None:
        response = await self._post(client, hr_principal(org_id), world["rep_signal"], self.BOTH)
        assert response.status_code == 200
        assert response.json() == {"ip": REP_IP, "device_id": DEVICE_A}

    async def test_only_the_requested_fields_are_opened(
        self, client: AsyncClient, org_id, world
    ) -> None:
        response = await self._post(
            client, hr_principal(org_id), world["rep_signal"], {"fields": ["ip"]}
        )
        assert response.json() == {"ip": REP_IP, "device_id": None}

    async def test_a_repeated_field_is_the_same_as_one(
        self, client: AsyncClient, session, org_id, world
    ) -> None:
        response = await self._post(
            client, hr_principal(org_id), world["rep_signal"], {"fields": ["ip", "ip"]}
        )
        assert response.status_code == 200
        (row,) = await _audit(session, "entity.sensitive_read")
        assert row.after == {"fields": ["ip"]}

    async def test_every_unmask_writes_an_audit_row_naming_fields_never_values(
        self, client: AsyncClient, session, org_id, world
    ) -> None:
        principal = hr_principal(org_id)
        for _ in range(2):
            assert (
                await self._post(client, principal, world["rep_signal"], self.BOTH)
            ).status_code == 200

        rows = await _audit(session, "entity.sensitive_read")
        assert len(rows) == 2  # every time, not once per signal or once per session
        for row in rows:
            assert row.entity == "punch_signal"
            assert row.entity_id == world["rep_signal"]
            assert row.actor_id == principal.sub
            assert row.after == {"fields": ["device_id", "ip"]}
            assert REP_IP not in str(row.after) and DEVICE_A not in str(row.after)

    async def test_an_empty_signal_still_leaves_the_audit_row(
        self, client: AsyncClient, session, org_id, world
    ) -> None:
        """The audited act is the attempt to read, not whether a value happened to exist."""
        record_id = uuid.uuid4()
        signal_id = uuid.uuid4()
        async with session.begin():
            session.add(
                AttendanceRecord(
                    id=record_id,
                    organization_id=org_id,
                    employee_id=world["rep"],
                    work_date=date(2020, 2, 2),
                    check_in_at=datetime.now(UTC),
                    source="web",
                    work_mode="office",
                    status="present",
                )
            )
            await session.flush()
            session.add(
                PunchSignal(
                    id=signal_id,
                    organization_id=org_id,
                    attendance_id=record_id,
                    employee_id=world["rep"],
                    kind="check_in",
                    punched_at=datetime.now(UTC),
                )
            )
        response = await self._post(client, hr_principal(org_id), signal_id, self.BOTH)
        assert response.json() == {"ip": None, "device_id": None}
        assert len(await _audit(session, "entity.sensitive_read")) == 1

    async def test_a_manager_may_unmask_their_reports_punch_if_granted_at_team(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(org_id, world["mgr"], [f"{SENSITIVE}:TEAM"], amr=["otp"])
        response = await self._post(client, principal, world["rep_signal"], self.BOTH)
        assert response.status_code == 200
        assert response.json()["ip"] == REP_IP

    async def test_a_manager_may_not_unmask_someone_elses_report(
        self, client: AsyncClient, session, org_id, world
    ) -> None:
        """The named vulnerability: a scoped list beside an unguarded detail. `mgr` can name the
        stranger's signal by id and is refused *before* anything is opened or audited."""
        principal = principal_for(org_id, world["mgr"], [f"{SENSITIVE}:TEAM"], amr=["otp"])
        response = await self._post(client, principal, world["stranger_signal"], self.BOTH)
        assert response.status_code == 403
        assert STRANGER_IP not in response.text and DEVICE_B not in response.text
        assert await _audit(session, "entity.sensitive_read") == []

    async def test_an_exited_reports_punch_is_out_of_a_managers_reach(
        self, client: AsyncClient, session, org_id, world
    ) -> None:
        async with session.begin():
            await services.upsert_team_membership_bulk(
                session,
                organization_id=org_id,
                rows=[
                    services.TeamMembershipWrite(
                        world["rep"], world["mgr"], "exited", 2, datetime.now(UTC)
                    )
                ],
            )
        principal = principal_for(org_id, world["mgr"], [f"{SENSITIVE}:TEAM"], amr=["otp"])
        response = await self._post(client, principal, world["rep_signal"], self.BOTH)
        assert response.status_code == 403

    async def test_an_own_grant_reaches_only_ones_own_punch(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(org_id, world["rep"], [f"{SENSITIVE}:OWN"], amr=["otp"])
        assert (
            await self._post(client, principal, world["rep_signal"], self.BOTH)
        ).status_code == 200
        assert (
            await self._post(client, principal, world["stranger_signal"], self.BOTH)
        ).status_code == 403

    async def test_a_location_scoped_grant_reaches_only_its_sites(
        self, client: AsyncClient, org_id, world
    ) -> None:
        principal = principal_for(
            org_id, uuid.uuid4(), [f"{SENSITIVE}:LOCATION"], amr=["otp"], locations=["MUM"]
        )
        assert (
            await self._post(client, principal, world["rep_signal"], self.BOTH)
        ).status_code == 200
        assert (
            await self._post(client, principal, world["stranger_signal"], self.BOTH)
        ).status_code == 403

    async def test_another_tenants_signal_is_a_404_not_a_403(
        self, client: AsyncClient, admin_session, org_id, world
    ) -> None:
        other_org = uuid.uuid4()
        record_id, signal_id = uuid.uuid4(), uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                AttendanceRecord.__table__.insert().values(
                    id=record_id,
                    organization_id=other_org,
                    employee_id=uuid.uuid4(),
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
                    employee_id=uuid.uuid4(),
                    kind="check_in",
                    punched_at=datetime.now(UTC),
                    ip_enc=b"\x01" + b"x" * 40,
                )
            )
        response = await self._post(client, hr_principal(org_id), signal_id, self.BOTH)
        # Confirming an id exists in some other organization is itself a disclosure.
        assert response.status_code == 404

    async def test_an_unknown_signal_is_a_404(self, client: AsyncClient, org_id, world) -> None:
        response = await self._post(client, hr_principal(org_id), uuid.uuid4(), self.BOTH)
        assert response.status_code == 404

    async def test_a_malformed_id_is_a_422(self, client: AsyncClient, org_id, world) -> None:
        response = await self._post(client, hr_principal(org_id), "not-a-uuid", self.BOTH)
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"fields": []},
            {"fields": ["ip", "device_id", "ip_enc"]},
            {"fields": ["password"]},
            {"fields": ["ip"], "reason": "curious"},
            {"fields": "ip"},
            [],
        ],
    )
    async def test_a_bad_body_is_a_422_and_audits_nothing(
        self, client: AsyncClient, session, org_id, world, body: Any
    ) -> None:
        response = await self._post(client, hr_principal(org_id), world["rep_signal"], body)
        assert response.status_code == 422
        assert await _audit(session, "entity.sensitive_read") == []

    async def test_a_ciphertext_that_will_not_open_reveals_nothing_and_audits_nothing(
        self, session, admin_session, org_id, world
    ) -> None:
        """A tampered or wrong-key envelope raises `PiiError`: nothing is revealed, and because the
        audit row shares the transaction it rolls back with it — no record of a read that returned
        nothing, and no message about ciphertext for the caller to learn from."""
        async with admin_session.begin():
            await admin_session.execute(
                PunchSignal.__table__.update()
                .where(PunchSignal.__table__.c.id == world["rep_signal"])
                .values(ip_enc=b"\x01" + b"\x00" * 60)
            )
        ctx = ctx_for(org_id, uuid.uuid4(), SENSITIVE, Scope.ORG)
        with pytest.raises(PiiError):
            async with session.begin():
                signal = await session.get(PunchSignal, world["rep_signal"])
                assert signal is not None
                await signals.unmask_signal(session, ctx, signal, ["ip"])
        assert await _audit(session, "entity.sensitive_read") == []


# --------------------------------------------------------------------- the settings


class TestSettings:
    URL = "/api/v1/attendance/signal-settings"

    async def test_an_organization_that_never_saved_one_reads_back_every_rule_off(
        self, client: AsyncClient, org_id
    ) -> None:
        with as_principal(hr_principal(org_id)):
            response = await client.get(self.URL)
        assert response.status_code == 200
        assert response.json() == {
            "detect_mock_location": False,
            "detect_impossible_travel": False,
            "detect_shared_device": False,
            "detect_ip_outside_allowed": False,
            "max_travel_speed_kmh": 900,
            "shared_device_window_hours": 24,
            "allowed_cidrs": [],
        }

    @pytest.mark.parametrize("method", ["get", "put"])
    async def test_no_credential_is_401(self, client: AsyncClient, method: str) -> None:
        response = await getattr(client, method)(
            self.URL, **({"json": {}} if method == "put" else {})
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("method", ["get", "put"])
    async def test_no_entitlement_is_402(self, client: AsyncClient, org_id, method: str) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [f"{SHIFT_WRITE}:ORG"], entitlements=[])
        with as_principal(principal):
            response = await getattr(client, method)(
                self.URL, **({"json": {}} if method == "put" else {})
            )
        assert response.status_code == 402

    @pytest.mark.parametrize("method", ["get", "put"])
    @pytest.mark.parametrize(
        "grants",
        [
            [],
            [f"{SHIFT_READ}:ORG"],
            [f"{SHIFT_WRITE}:OWN"],
            [f"{SHIFT_WRITE}:TEAM"],
            [f"{FLAG_READ}:ORG", f"{SENSITIVE}:ORG"],
        ],
    )
    async def test_only_the_org_time_admin_may_read_or_change_the_rules(
        self, client: AsyncClient, org_id, method: str, grants: list[str]
    ) -> None:
        """The thresholds and ranges tell a person exactly how to stay under them, so even reading
        them is an administrator's. `shift.write` at less than ORG is refused by the floor."""
        principal = principal_for(org_id, uuid.uuid4(), grants, amr=["otp"])
        with as_principal(principal):
            response = await getattr(client, method)(
                self.URL, **({"json": {}} if method == "put" else {})
            )
        assert response.status_code == 403

    async def test_a_put_is_stored_canonicalised_and_read_back(
        self, client: AsyncClient, session, org_id
    ) -> None:
        body = {
            "detect_mock_location": True,
            "detect_ip_outside_allowed": True,
            "max_travel_speed_kmh": 250,
            "shared_device_window_hours": 48,
            "allowed_cidrs": ["203.0.113.77/24", "203.0.113.0/24", "2001:DB8::/32"],
        }
        with as_principal(hr_principal(org_id)):
            put = await client.put(self.URL, json=body)
            got = await client.get(self.URL)
        assert put.status_code == 200
        expected = {
            **body,
            "detect_impossible_travel": False,
            "detect_shared_device": False,
            "allowed_cidrs": ["203.0.113.0/24", "2001:db8::/32"],
        }
        assert put.json() == expected
        assert got.json() == expected

    async def test_a_put_is_a_full_replacement(self, client: AsyncClient, org_id) -> None:
        with as_principal(hr_principal(org_id)):
            await client.put(
                self.URL, json={"detect_mock_location": True, "max_travel_speed_kmh": 250}
            )
            replaced = await client.put(self.URL, json={"detect_shared_device": True})
        assert replaced.json()["detect_mock_location"] is False
        assert replaced.json()["max_travel_speed_kmh"] == 900
        assert replaced.json()["detect_shared_device"] is True

    async def test_saving_the_same_body_twice_leaves_one_row(
        self, client: AsyncClient, session, org_id
    ) -> None:
        with as_principal(hr_principal(org_id)):
            first = await client.put(self.URL, json={"detect_mock_location": True})
            second = await client.put(self.URL, json={"detect_mock_location": True})
        assert first.json() == second.json()
        async with session.begin():
            rows = (
                await session.execute(select(func.count()).select_from(SignalSettings))
            ).scalar_one()
        assert rows == 1

    async def test_each_save_is_audited_with_the_rules_as_they_were_and_are(
        self, client: AsyncClient, session, org_id
    ) -> None:
        principal = hr_principal(org_id)
        with as_principal(principal):
            await client.put(self.URL, json={"detect_mock_location": True})
            await client.put(
                self.URL,
                json={"detect_shared_device": True, "shared_device_window_hours": 12},
            )
        first, second = sorted(
            await _audit(session, "attendance.signal_settings.updated"), key=lambda r: r.at
        )
        assert first.before["detect_mock_location"] is False
        assert first.after["detect_mock_location"] is True
        assert second.before["detect_mock_location"] is True
        assert second.after["detect_mock_location"] is False
        assert second.after["shared_device_window_hours"] == 12
        assert first.actor_id == principal.sub
        assert first.entity == "signal_settings"

    @pytest.mark.parametrize(
        "body",
        [
            {"detect_everything": True},
            {"block_on_flag": True},
            {"allowed_cidrs": ["not-a-range"]},
            {"allowed_cidrs": ["203.0.113.0/33"]},
            {"allowed_cidrs": ["0.0.0.0/0"]},
            {"allowed_cidrs": ["::/0"]},
            {"allowed_cidrs": "203.0.113.0/24"},
            {"allowed_cidrs": [f"10.{i}.0.0/16" for i in range(51)]},
            {"detect_ip_outside_allowed": True},
            {"detect_ip_outside_allowed": True, "allowed_cidrs": []},
            {"max_travel_speed_kmh": 9},
            {"max_travel_speed_kmh": 3001},
            {"max_travel_speed_kmh": "fast"},
            {"shared_device_window_hours": 0},
            {"shared_device_window_hours": 169},
            {"detect_mock_location": "maybe"},
        ],
    )
    async def test_a_bad_body_is_a_422_and_changes_nothing(
        self, client: AsyncClient, session, org_id, body: dict[str, Any]
    ) -> None:
        with as_principal(hr_principal(org_id)):
            response = await client.put(self.URL, json=body)
        assert response.status_code == 422
        async with session.begin():
            rows = (
                await session.execute(select(func.count()).select_from(SignalSettings))
            ).scalar_one()
        assert rows == 0

    async def test_there_is_no_switch_that_makes_a_flag_block_a_punch(
        self, client: AsyncClient, org_id
    ) -> None:
        """Observe-only is a property of the contract: a client that asks for blocking is refused
        as an unknown field rather than silently ignored."""
        with as_principal(hr_principal(org_id)):
            for field in ("block", "blocking", "enforce", "reject_flagged_punches"):
                response = await client.put(self.URL, json={field: True})
                assert response.status_code == 422

    async def test_one_tenants_rules_are_not_anothers(
        self, client: AsyncClient, admin_session, org_id
    ) -> None:
        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                SignalSettings.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    detect_mock_location=True,
                    detect_impossible_travel=True,
                    detect_shared_device=True,
                    detect_ip_outside_allowed=False,
                    max_travel_speed_kmh=77,
                    shared_device_window_hours=5,
                    allowed_cidrs=[],
                )
            )
        with as_principal(hr_principal(org_id)):
            mine = (await client.get(self.URL)).json()
            await client.put(self.URL, json={"detect_mock_location": True})
        assert mine["max_travel_speed_kmh"] == 900 and not mine["detect_impossible_travel"]
        with as_principal(hr_principal(other_org)):
            theirs = (await client.get(self.URL)).json()
        assert theirs["max_travel_speed_kmh"] == 77 and theirs["detect_shared_device"] is True

    async def test_the_database_holds_one_settings_row_per_organization(
        self, session, org_id
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        await configure(session, org_id, detect_mock_location=True)
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    SignalSettings(
                        organization_id=org_id,
                        detect_mock_location=False,
                        detect_impossible_travel=False,
                        detect_shared_device=False,
                        detect_ip_outside_allowed=False,
                        max_travel_speed_kmh=900,
                        shared_device_window_hours=24,
                        allowed_cidrs=[],
                    )
                )

    async def test_turning_a_rule_on_flags_the_next_punch_end_to_end(
        self, client: AsyncClient, session, org_id
    ) -> None:
        me = uuid.uuid4()
        with as_principal(hr_principal(org_id)):
            await client.put(self.URL, json={"detect_mock_location": True})
        with as_principal(principal_for(org_id, me, ["hrms.time.attendance.write:OWN"])):
            response = await client.post(
                "/api/v1/attendance/check-in",
                json={"work_mode": "office", "source": "web", "mock_location": True},
            )
        assert response.status_code == 201
        assert [f.reason_code for f in await all_flags(session)] == ["mock_location"]


# --------------------------------------------------------------------- the punch inputs


class TestPunchInputs:
    IN = "/api/v1/attendance/check-in"
    OUT = "/api/v1/attendance/check-out"

    def _writer(self, org_id: uuid.UUID) -> Principal:
        return principal_for(org_id, uuid.uuid4(), ["hrms.time.attendance.write:OWN"])

    @pytest.mark.parametrize(
        "extra",
        [
            {"device_id": DEVICE_A},
            {"employee_id": str(uuid.uuid4())},
            {"ip": "203.0.113.9"},
            {"flags": []},
            {"mock": True},
        ],
    )
    async def test_an_unknown_field_on_check_in_is_refused_not_ignored(
        self, client: AsyncClient, org_id, extra: dict[str, Any]
    ) -> None:
        """A client that misspells a signal would otherwise stop reporting it and nobody would know;
        one that sends `employee_id` would believe it was punching for someone else."""
        with as_principal(self._writer(org_id)):
            response = await client.post(self.IN, json={"work_mode": "office", **extra})
        assert response.status_code == 422

    async def test_an_unknown_field_on_check_out_is_refused(
        self, client: AsyncClient, org_id
    ) -> None:
        with as_principal(self._writer(org_id)):
            response = await client.post(self.OUT, json={"device_id": DEVICE_A})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "field",
        [
            {"device_id_hash": "short"},
            {"device_id_hash": "x" * 129},
            {"device_id_hash": "has spaces in it 1234567890"},
            {"device_id_hash": "<script>alert(1)</script>"},
            {"location_accuracy_m": "-1"},
            {"location_accuracy_m": "100001"},
            {"location_accuracy_m": "wide"},
            {"mock_location": {"yes": True}},
        ],
    )
    async def test_a_malformed_signal_is_refused_at_the_boundary(
        self, client: AsyncClient, org_id, field: dict[str, Any]
    ) -> None:
        with as_principal(self._writer(org_id)):
            response = await client.post(self.IN, json={"work_mode": "office", **field})
        assert response.status_code == 422

    async def test_the_signals_are_optional(self, client: AsyncClient, org_id) -> None:
        with as_principal(self._writer(org_id)):
            response = await client.post(self.IN, json={"work_mode": "office", "source": "web"})
        assert response.status_code == 201

    async def test_a_junk_forwarded_header_does_not_fail_the_punch(
        self, client: AsyncClient, session, org_id
    ) -> None:
        """The audit row's `ip` column is INET: a header the client typed used to reach the INSERT
        and turn a punch into a 500. It is now recorded as unknown, with the rules on or off."""
        await configure(
            session, org_id, detect_ip_outside_allowed=True, allowed_cidrs=["203.0.113.0/24"]
        )
        for junk in ("unknown", "203.0.113.9:443", "'; DROP TABLE x;--"):
            with as_principal(self._writer(org_id)):
                response = await client.post(
                    self.IN,
                    json={"work_mode": "office", "source": "web"},
                    headers={"X-Forwarded-For": junk},
                )
            assert response.status_code == 201, junk
        assert await all_flags(session) == []

    async def test_a_flagged_punch_answers_exactly_as_an_unflagged_one_does(
        self, client: AsyncClient, session, org_id
    ) -> None:
        await configure(session, org_id, detect_mock_location=True)
        me = uuid.uuid4()
        with as_principal(principal_for(org_id, me, ["hrms.time.attendance.write:OWN"])):
            response = await client.post(
                self.IN, json={"work_mode": "office", "source": "web", "mock_location": True}
            )
        assert response.status_code == 201
        assert set(response.json()) == {
            "id",
            "employee_id",
            "work_date",
            "check_in_at",
            "check_out_at",
            "source",
            "location_code",
            "shift_id",
            "work_mode",
            "status",
            "check_in_lat",
            "check_in_lng",
            "check_out_lat",
            "check_out_lng",
            "notes",
            "created_at",
            "regularization",
            "allowed_transitions",
        }
        assert len(await all_flags(session)) == 1

    async def test_the_seed_helper_records_history_the_rules_can_read(
        self, session, org_id
    ) -> None:
        """Guards the fixtures the rule tests lean on: a seeded signal is a real, readable row."""
        me = uuid.uuid4()
        await seed_signal(session, org_id, me, ago=timedelta(hours=1), device=DEVICE_A)
        (signal,) = await all_signals(session)
        assert signal.device_index == signals.device_index_for(DEVICE_A)
        assert signal.employee_id == me
