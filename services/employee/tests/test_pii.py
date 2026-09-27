"""Sensitive PII: sealed on write, masked by default, unmasked only on a stepped-up, audited act.

These are the tests that make "encrypted and masked by default; unmasking needs its own
permission and writes an audit row every time" real for the employee service. They run against
real PostgreSQL as the app role, like the rest of the suite, so the `_enc` columns hold genuine
ciphertext and the audit row is a genuine committed row.

Written as negative assertions wherever the property is a refusal: unmasking is refused without
the permission, refused without the second factor, and refused across a scope boundary. The one
positive path — a successful unmask — is asserted to leave the audit trail the rule demands.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from app.models import AuditLog, Employee
from app.schemas import EmployeeIn, EmployeePiiIn
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

# Real-shaped secrets, chosen so a leak of any of them into a response body or audit row is
# unambiguous when a test greps for it.
BANK = "000123456789"
PAN = "ABCDE1234F"  # pragma: allowlist secret
EMAIL = "priya.sharma@personal.example"
DOB = date(1990, 5, 15)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _hire(
    session: AsyncSession, org_id: uuid.UUID, employee_no: str, **pii: object
) -> Employee:
    """Onboard through the real write path, optionally sealing PII on the way in."""
    ctx = ctx_for(org_id, Employee(id=uuid.uuid4()), "hrms.people.employee.write", Scope.ORG)
    payload = EmployeeIn(
        employee_no=employee_no,
        full_name="Priya Sharma",
        pii=EmployeePiiIn(**pii) if pii else None,  # type: ignore[arg-type]
    )
    return await services.create_employee(session, ctx, payload)


@pytest_asyncio.fixture
async def sealed(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Employee]:
    """One employee with sealed PII, and a second, plain employee to stand in as 'somebody else'
    for the scope tests."""
    async with session.begin():
        target = await _hire(
            session,
            org_id,
            "EMP0700",
            bank_account=BANK,
            pan=PAN,
            personal_email=EMAIL,
            date_of_birth=DOB,
            bank_ifsc="HDFC0001234",
        )
        other = await _hire(session, org_id, "EMP0701")
    return {"target": target, "other": other}


# --------------------------------------------------------------------- seal on write


class TestSealOnWrite:
    async def test_pii_is_stored_as_ciphertext_and_a_mask(self, session, org_id, sealed) -> None:
        """The `_enc` column holds ciphertext — the plaintext bytes are not in it — and the
        `_masked` column holds a hint that is not the whole secret."""
        row = await selectors.get_employee_pii(session, org_id, sealed["target"].id)
        assert row is not None

        assert row.bank_account_enc is not None
        assert BANK.encode() not in row.bank_account_enc
        assert row.pan_enc is not None
        assert PAN.encode() not in row.pan_enc

        assert row.bank_account_masked is not None
        assert BANK not in row.bank_account_masked
        assert row.bank_account_masked.endswith("6789")
        # An IFSC is a public branch code, stored whole in the clear by design.
        assert row.bank_ifsc == "HDFC0001234"
        assert row.date_of_birth_masked == "••/••/1990"

    async def test_a_sealed_value_opens_back_to_the_original_through_the_service(
        self, session, org_id, sealed
    ) -> None:
        """Round-trip through the service: what was sealed on write comes back intact on unmask."""
        target = sealed["target"]
        ctx = ctx_for(org_id, target, "hrms.people.employee.read_sensitive", Scope.ORG)
        async with session.begin():
            revealed = await services.unmask_employee_pii(
                session, ctx, target, ["bank_account", "pan", "date_of_birth"]
            )
        assert revealed.bank_account == BANK
        assert revealed.pan == PAN
        assert revealed.date_of_birth == DOB


# --------------------------------------------------------------------- masked by default


class TestDefaultReadIsMasked:
    async def test_the_detail_response_returns_masks_and_never_plaintext(
        self, client, org_id, sealed
    ) -> None:
        """The default read path must not decrypt: the detail body carries masks, and none of the
        plaintext secrets appear anywhere in it."""
        target = sealed["target"]
        reader = principal_for(org_id, target, ["hrms.people.employee.read:ORG"])
        with as_principal(reader):
            response = await client.get(f"/api/v1/employees/{target.id}")

        assert response.status_code == 200
        assert BANK not in response.text
        assert PAN not in response.text
        assert EMAIL not in response.text

        pii = response.json()["pii"]
        assert pii["bank_account"].endswith("6789")
        assert pii["personal_email"] == "p•••@p•••"
        assert pii["bank_ifsc"] == "HDFC0001234"


# --------------------------------------------------------------------- unmask authorization


class TestUnmaskAuthorization:
    def _body(self, *fields: str) -> dict[str, list[str]]:
        return {"fields": list(fields)}

    async def test_unmask_requires_the_read_sensitive_permission(
        self, client, org_id, sealed
    ) -> None:
        """Holding plain read — even at ORG, even stepped up — is not enough. The wildcard match
        keeps the dot, so `read` never satisfies `read_sensitive`."""
        target = sealed["target"]
        principal = principal_for(org_id, target, ["hrms.people.employee.read:ORG"], amr=["otp"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/employees/{target.id}/pii/unmask", json=self._body("bank_account")
            )
        assert response.status_code == 403

    async def test_unmask_requires_the_step_up(self, client, org_id, sealed) -> None:
        """The permission is held, but the caller has not presented a second factor. `amr` gates
        the dangerous action; without `otp` it is refused."""
        target = sealed["target"]
        principal = principal_for(
            org_id, target, ["hrms.people.employee.read_sensitive:ORG"], amr=[]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/employees/{target.id}/pii/unmask", json=self._body("bank_account")
            )
        assert response.status_code == 403

    async def test_own_scope_can_unmask_self(self, client, org_id, sealed) -> None:
        """A person may reveal their own full details. The subject reference is their own record,
        so OWN reaches it."""
        target = sealed["target"]
        principal = principal_for(
            org_id, target, ["hrms.people.employee.read_sensitive:OWN"], amr=["otp"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/employees/{target.id}/pii/unmask", json=self._body("bank_account", "pan")
            )
        assert response.status_code == 200
        body = response.json()
        assert body["bank_account"] == BANK
        assert body["pan"] == PAN

    async def test_a_session_that_gave_a_code_at_sign_in_still_passes_this_route_risk_5(
        self, client, org_id, sealed
    ) -> None:
        """ADR-0023 risk 5, pinned as it is today. This route requires `otp` alone, and a session
        that gave a code at sign-in carries `otp` on every token it mints for its whole life — so
        with an authenticator enrolled (which turns MFA on for every later sign-in), that session's
        ordinary token unmasks here with no fresh code. The CRM reveal routes also require the step-
        up marker and refuse it; this one does not, because its UI does not yet run the step-up.
        When the follow-up wires `useStepUp` here and adds the marker to the guard, THIS TEST FAILS
        — which is the point: rewrite it as the refusal it should then be."""
        target = sealed["target"]
        principal = principal_for(
            org_id,
            target,
            ["hrms.people.employee.read_sensitive:OWN"],
            amr=["pwd", "otp"],  # a login-time session: `otp`, and no step-up marker
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/employees/{target.id}/pii/unmask", json=self._body("bank_account", "pan")
            )
        assert response.status_code == 200
        assert response.json()["bank_account"] == BANK

    async def test_own_scope_cannot_unmask_another(self, client, org_id, sealed) -> None:
        """OWN reaches only the caller's own record. Aimed at somebody else, it is refused by the
        object-level scope check — the named vulnerability, closed for the unmask route too."""
        target, other = sealed["target"], sealed["other"]
        # The caller is `other`; their OWN scope must not reach `target`.
        principal = principal_for(
            org_id, other, ["hrms.people.employee.read_sensitive:OWN"], amr=["otp"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/employees/{target.id}/pii/unmask", json=self._body("bank_account")
            )
        assert response.status_code == 403

    async def test_org_scope_can_unmask_anyone_in_org(self, client, org_id, sealed) -> None:
        target, other = sealed["target"], sealed["other"]
        # The caller is `other`, but ORG scope reaches every record in the organization.
        principal = principal_for(
            org_id, other, ["hrms.people.employee.read_sensitive:ORG"], amr=["otp"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/employees/{target.id}/pii/unmask", json=self._body("bank_account")
            )
        assert response.status_code == 200
        assert response.json()["bank_account"] == BANK

    async def test_another_tenant_cannot_unmask_at_all(self, client, org_id, sealed) -> None:
        """A principal acting for a different organization cannot even name the row: the
        org-scoped selector returns nothing and the route answers 404, disclosing neither the
        employee's existence nor a byte of their PII."""
        target = sealed["target"]
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org, target, ["hrms.people.employee.read_sensitive:ORG"], amr=["otp"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/employees/{target.id}/pii/unmask", json=self._body("bank_account")
            )
        assert response.status_code == 404
        assert BANK not in response.text


# --------------------------------------------------------------------- the audit trail


class TestUnmaskIsAudited:
    async def test_a_successful_unmask_writes_a_sensitive_read_row_naming_the_fields(
        self, client, session, org_id, sealed
    ) -> None:
        """Unmasking is itself an auditable read. On success an `entity.sensitive_read` row is
        committed in the same transaction, naming exactly the fields revealed and never their
        values — `record_sensitive_read`'s first real caller."""
        target = sealed["target"]
        principal = principal_for(
            org_id, target, ["hrms.people.employee.read_sensitive:OWN"], amr=["otp"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/employees/{target.id}/pii/unmask",
                json={"fields": ["bank_account", "pan"]},
            )
        assert response.status_code == 200

        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "entity.sensitive_read",
                        AuditLog.entity == "employee",
                        AuditLog.entity_id == target.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].after == {"fields": ["bank_account", "pan"]}
        # The audit payload names fields only — no secret leaked into the trail.
        assert BANK not in str(rows[0].after)
        assert PAN not in str(rows[0].after)

    async def test_the_onboarding_audit_names_pii_fields_never_their_values(
        self, session, org_id, sealed
    ) -> None:
        """The write side is just as careful: onboarding with PII records which sensitive fields
        were set, not what they were sealed to."""
        target = sealed["target"]
        row = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "employee.onboarded",
                    AuditLog.entity_id == target.id,
                )
            )
        ).scalar_one()
        assert set(row.after["pii_fields"]) == {
            "bank_account",
            "pan",
            "personal_email",
            "date_of_birth",
            "bank_ifsc",
        }
        assert BANK not in str(row.after)
        assert PAN not in str(row.after)
