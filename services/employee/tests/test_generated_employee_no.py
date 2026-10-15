"""An employee record can be created without picking a number.

"Create my employee record" (the owner of a self-serve organization has none, and nothing to link
their login to) must not make them invent an employee number. Omitted, the next free `EMP####` is
generated; supplied, it is used exactly as before. Uniqueness is still the table's constraint.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


class TestAGeneratedEmployeeNumber:
    async def test_it_is_generated_when_omitted(self, client, org_id, people) -> None:
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.write:ORG"])
        with as_principal(principal):
            first = await client.post("/api/v1/employees", json={"full_name": "First Hire"})
            second = await client.post("/api/v1/employees", json={"full_name": "Second Hire"})
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        a, b = first.json()["employee_no"], second.json()["employee_no"]
        assert a.startswith("EMP") and b.startswith("EMP") and a != b
        assert int(b[3:]) == int(a[3:]) + 1

    async def test_it_continues_after_the_highest_existing_number(
        self, client, org_id, people
    ) -> None:
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.write:ORG"])
        with as_principal(principal):
            await client.post(
                "/api/v1/employees", json={"employee_no": "EMP0500", "full_name": "Numbered"}
            )
            response = await client.post("/api/v1/employees", json={"full_name": "Next"})
        assert response.json()["employee_no"] == "EMP0501"

    async def test_a_supplied_number_is_still_used_and_still_unique(
        self, client, org_id, people
    ) -> None:
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.write:ORG"])
        body = {"employee_no": f"X{uuid.uuid4().hex[:6]}", "full_name": "Chosen"}
        with as_principal(principal):
            first = await client.post("/api/v1/employees", json=body)
            again = await client.post("/api/v1/employees", json=body)
        assert first.status_code == 201 and first.json()["employee_no"] == body["employee_no"]
        assert again.status_code == 409

    async def test_a_reader_still_cannot_create_one(self, client, org_id, people) -> None:
        principal = principal_for(org_id, people["report"], ["hrms.people.employee.read:OWN"])
        with as_principal(principal):
            response = await client.post("/api/v1/employees", json={"full_name": "Intruder"})
        assert response.status_code == 403

    async def test_a_blank_number_is_refused_not_generated(self, client, org_id, people) -> None:
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/employees", json={"employee_no": "", "full_name": "Blank"}
            )
        assert response.status_code == 422
