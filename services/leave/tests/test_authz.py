"""Authorization over HTTP, against real PostgreSQL — one negative case per endpoint, plus the
object-level check that stops a correct list filter from being undone by an unguarded detail
route (the named vulnerability `attendance/tests/test_authz.py`'s docstring describes).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.main import app
from app.schemas import LeaveBalanceGrantIn, LeaveRequestIn, LeaveTypeIn
from platform_core.auth import Principal, Scope, get_principal
from platform_core.db import dispose_engine, init_engine
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """One test in this module (`test_a_manager_reaches_a_direct_reports_request_at_team_scope`)
    seeds the employee directory by calling a consumer handler directly, and that handler opens
    its own `session_scope`, which needs the process engine initialised — the same fixture
    `tests/test_consumers.py` uses to stand in for the app lifespan."""
    init_engine(settings())
    yield
    await dispose_engine()


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _leave_type(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_type.write", Scope.ORG)
    async with session.begin():
        leave_type = await services.create_leave_type(
            session, ctx, LeaveTypeIn(code="EL", name="Earned leave")
        )
        leave_type_id = leave_type.id
    return leave_type_id


async def _pending_request(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID, leave_type_id: uuid.UUID
) -> uuid.UUID:
    grant_ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_balance.write", Scope.ORG)
    async with session.begin():
        await services.grant_leave_balance(
            session,
            grant_ctx,
            LeaveBalanceGrantIn(employee_id=employee_id, leave_type_id=leave_type_id, half_days=10),
        )
    ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)
    async with session.begin():
        req = await services.apply_for_leave(
            session,
            ctx,
            LeaveRequestIn(
                leave_type_id=leave_type_id,
                start_date=date(2026, 9, 21),
                end_date=date(2026, 9, 22),
                reason="authz fixture",
            ),
        )
        req_id = req.id
    return req_id


class TestUnauthenticatedAndUnentitled:
    async def test_unauthenticated_is_rejected(self, client) -> None:
        response = await client.get("/api/v1/leave/types")
        assert response.status_code == 401

    async def test_missing_the_permission_is_forbidden(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [])
        with as_principal(principal):
            response = await client.get("/api/v1/leave/types")
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id
    ) -> None:
        principal = principal_for(
            org_id, uuid.uuid4(), ["hrms.time.leave_type.read:ORG"], entitlements=[]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/leave/types")
        assert response.status_code == 402


class TestLeaveTypeAuthz:
    async def test_reading_types_needs_no_write_permission(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/leave/types")
        assert response.status_code == 200

    async def test_a_reader_cannot_create_a_type(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.read:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/leave/types", json={"code": "SL", "name": "Sick leave"}
            )
        assert response.status_code == 403

    async def test_an_org_writer_may_create_a_type(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/leave/types", json={"code": "SL", "name": "Sick leave"}
            )
        assert response.status_code == 201


class TestLeaveSettingsAuthz:
    async def test_a_reader_may_view_settings(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/leave/settings")
        assert response.status_code == 200

    async def test_a_reader_cannot_change_the_timezone(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.read:ORG"])
        with as_principal(principal):
            response = await client.put("/api/v1/leave/settings", json={"timezone": "Asia/Kolkata"})
        assert response.status_code == 403

    async def test_missing_the_permission_entirely_is_forbidden_on_both_verbs(
        self, client, org_id
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [])
        with as_principal(principal):
            get_response = await client.get("/api/v1/leave/settings")
            put_response = await client.put(
                "/api/v1/leave/settings", json={"timezone": "Asia/Kolkata"}
            )
        assert get_response.status_code == 403
        assert put_response.status_code == 403

    async def test_an_org_writer_may_change_the_timezone(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.write:ORG"])
        with as_principal(principal):
            response = await client.put("/api/v1/leave/settings", json={"timezone": "Asia/Kolkata"})
        assert response.status_code == 200
        assert response.json()["timezone"] == "Asia/Kolkata"


class TestLeaveTypeMutationAuthz:
    async def test_a_reader_cannot_patch_a_type(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.read:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/leave/types/{leave_type_id}", json={"name": "Renamed"}
            )
        assert response.status_code == 403

    async def test_a_reader_cannot_delete_a_type(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.read:ORG"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/leave/types/{leave_type_id}")
        assert response.status_code == 403

    async def test_an_org_writer_may_patch_a_type(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/leave/types/{leave_type_id}", json={"name": "Renamed"}
            )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"

    async def test_an_org_writer_may_delete_a_type(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.write:ORG"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/leave/types/{leave_type_id}")
        assert response.status_code == 204

    async def test_patching_an_unknown_type_is_not_found_not_a_500(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/leave/types/{uuid.uuid4()}", json={"name": "Renamed"}
            )
        assert response.status_code == 404


class TestLeaveBalanceAuthz:
    async def test_an_own_reader_cannot_grant_a_balance(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_balance.read:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/leave/balances/grant",
                json={
                    "employee_id": str(uuid.uuid4()),
                    "leave_type_id": str(leave_type_id),
                    "half_days": 10,
                },
            )
        assert response.status_code == 403

    async def test_an_org_writer_may_grant_a_balance(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_balance.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/leave/balances/grant",
                json={
                    "employee_id": str(uuid.uuid4()),
                    "leave_type_id": str(leave_type_id),
                    "half_days": 10,
                },
            )
        assert response.status_code == 201

    async def test_a_caller_cannot_read_another_employees_balance_at_own_scope(
        self, client, org_id, session: AsyncSession
    ) -> None:
        me = uuid.uuid4()
        someone_else = uuid.uuid4()
        principal = principal_for(org_id, me, ["hrms.time.leave_balance.read:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/leave/balances", params={"employee_id": str(someone_else)}
            )
        assert response.status_code == 200
        # OWN narrows the underlying query rather than 403ing the mismatched filter — the same
        # "narrow, don't leak" shape attendance's own `employee_id` list filter uses.
        assert response.json() == []

    async def test_reading_the_ledger_for_someone_else_is_forbidden_at_own_scope(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        me = uuid.uuid4()
        someone_else = uuid.uuid4()
        principal = principal_for(org_id, me, ["hrms.time.leave_balance.read:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/leave/balances/ledger",
                params={"employee_id": str(someone_else), "leave_type_id": str(leave_type_id)},
            )
        assert response.status_code == 403


class TestLeaveRequestAuthz:
    async def test_an_employee_may_apply_for_their_own_leave(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        grant_ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_balance.write", Scope.ORG)
        async with session.begin():
            await services.grant_leave_balance(
                session,
                grant_ctx,
                LeaveBalanceGrantIn(
                    employee_id=uuid.uuid4(), leave_type_id=leave_type_id, half_days=10
                ),
            )
        employee_id = uuid.uuid4()
        async with session.begin():
            await services.grant_leave_balance(
                session,
                grant_ctx,
                LeaveBalanceGrantIn(
                    employee_id=employee_id, leave_type_id=leave_type_id, half_days=10
                ),
            )
        principal = principal_for(org_id, employee_id, ["hrms.time.leave_request.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/leave/requests",
                json={
                    "leave_type_id": str(leave_type_id),
                    "start_date": "2026-09-21",
                    "end_date": "2026-09-22",
                    "reason": "family trip",
                },
            )
        assert response.status_code == 201
        assert response.json()["employee_id"] == str(employee_id)

    async def test_the_detail_route_refuses_another_employees_request_at_own_scope(
        self, client, org_id, session: AsyncSession
    ) -> None:
        """The named vulnerability: a correct list filter beside an unguarded detail view."""
        leave_type_id = await _leave_type(session, org_id)
        owner = uuid.uuid4()
        req_id = await _pending_request(session, org_id, owner, leave_type_id)

        caller = uuid.uuid4()
        principal = principal_for(org_id, caller, ["hrms.time.leave_request.read:OWN"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/leave/requests/{req_id}")
        assert response.status_code == 403

    async def test_the_owner_may_read_their_own_request(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        owner = uuid.uuid4()
        req_id = await _pending_request(session, org_id, owner, leave_type_id)

        principal = principal_for(org_id, owner, ["hrms.time.leave_request.read:OWN"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/leave/requests/{req_id}")
        assert response.status_code == 200

    async def test_an_org_reader_may_read_anyones_request(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        owner = uuid.uuid4()
        req_id = await _pending_request(session, org_id, owner, leave_type_id)

        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_request.read:ORG"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/leave/requests/{req_id}")
        assert response.status_code == 200

    async def test_a_manager_reaches_a_direct_reports_request_at_team_scope(
        self, client, org_id, session: AsyncSession
    ) -> None:
        from app.consumers import on_employee_onboarded
        from tests.helpers import employee_hierarchy_envelope

        leave_type_id = await _leave_type(session, org_id)
        owner = uuid.uuid4()
        manager = uuid.uuid4()
        await on_employee_onboarded(
            employee_hierarchy_envelope(
                organization_id=org_id,
                employee_id=owner,
                reporting_manager_id=manager,
                hierarchy_version=1,
            )
        )
        session.expire_all()
        req_id = await _pending_request(session, org_id, owner, leave_type_id)

        principal = principal_for(org_id, manager, ["hrms.time.leave_request.read:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/leave/requests/{req_id}")
        assert response.status_code == 200

        stranger = uuid.uuid4()
        principal = principal_for(org_id, stranger, ["hrms.time.leave_request.read:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/leave/requests/{req_id}")
        assert response.status_code == 403

    async def test_a_caller_cannot_cancel_someone_elses_request(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        owner = uuid.uuid4()
        req_id = await _pending_request(session, org_id, owner, leave_type_id)

        caller = uuid.uuid4()
        principal = principal_for(org_id, caller, ["hrms.time.leave_request.write:OWN"])
        with as_principal(principal):
            response = await client.post(f"/api/v1/leave/requests/{req_id}/cancel", json={})
        assert response.status_code == 403

    async def test_the_owner_may_cancel_their_own_request(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        owner = uuid.uuid4()
        req_id = await _pending_request(session, org_id, owner, leave_type_id)

        principal = principal_for(org_id, owner, ["hrms.time.leave_request.write:OWN"])
        with as_principal(principal):
            response = await client.post(f"/api/v1/leave/requests/{req_id}/cancel", json={})
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    async def test_only_manage_at_org_may_force_cancel_someone_elses_request(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        owner = uuid.uuid4()
        req_id = await _pending_request(session, org_id, owner, leave_type_id)

        # Holding only the self-service permission is refused outright — `manage` is a
        # different permission code, not a wider scope of the same one.
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_request.write:OWN"])
        with as_principal(principal):
            response = await client.post(f"/api/v1/leave/requests/{req_id}/force-cancel", json={})
        assert response.status_code == 403

        hr = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_request.manage:ORG"])
        with as_principal(hr):
            response = await client.post(f"/api/v1/leave/requests/{req_id}/force-cancel", json={})
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    async def test_an_own_scoped_caller_lists_only_their_own_requests(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        me = uuid.uuid4()
        someone_else = uuid.uuid4()
        my_request = await _pending_request(session, org_id, me, leave_type_id)
        await _pending_request(session, org_id, someone_else, leave_type_id)

        principal = principal_for(org_id, me, ["hrms.time.leave_request.read:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/leave/requests")
        assert response.status_code == 200
        body = response.json()
        ids = {item["id"] for item in body["items"]}
        assert ids == {str(my_request)}

    async def test_an_org_scoped_caller_lists_every_employees_requests(
        self, client, org_id, session: AsyncSession
    ) -> None:
        leave_type_id = await _leave_type(session, org_id)
        owner_a = uuid.uuid4()
        owner_b = uuid.uuid4()
        req_a = await _pending_request(session, org_id, owner_a, leave_type_id)
        req_b = await _pending_request(session, org_id, owner_b, leave_type_id)

        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_request.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/leave/requests")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert {str(req_a), str(req_b)} <= ids

    async def test_a_caller_with_no_subject_ref_cannot_apply(self, client, org_id) -> None:
        """Authenticated, entitled, permission-holding — but not an employee of this
        organization. A business rule, not a permission failure (422/400 shape, never a leak)."""
        principal = principal_for(org_id, None, ["hrms.time.leave_request.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/leave/requests",
                json={
                    "leave_type_id": str(uuid.uuid4()),
                    "start_date": "2026-09-21",
                    "end_date": "2026-09-21",
                    "reason": "no subject ref",
                },
            )
        assert response.status_code >= 400
        assert response.status_code != 500

    async def test_an_unknown_field_on_a_write_is_rejected(self, client, org_id) -> None:
        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.leave_type.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/leave/types",
                json={"code": "SL", "name": "Sick leave", "unexpected_field": "nope"},
            )
        assert response.status_code == 422
