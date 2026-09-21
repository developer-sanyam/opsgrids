"""The write surface: opening an account, adding a contact, extending the rep directory.

The writers themselves are already covered (`test_authz.py` for the domain rules,
`test_contact_phone_pii.py` for the sealed number). What is new here is the HTTP shell around
them, so these assert the three things a route adds and a service function cannot: that the
permission guarding it is the *right* one, that scope still binds when there is no row yet to
scope against, and that a malformed payload is refused before any of it runs.

Every endpoint carries a negative-authorization case (CLAUDE.md), and each one checks the
refusal left nothing behind — a 403 that still wrote the row is the failure worth catching,
and asserting only the status code would not catch it.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app import selectors
from app.main import app
from app.models import AuditLog, OutboxEvent
from platform_core.auth import Principal, get_principal
from platform_core.pii import BULLET
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

COMPANY_READ = "crm.book.company.read"
COMPANY_WRITE = "crm.book.company.write"
CONTACT_READ = "crm.book.contact.read"
CONTACT_WRITE = "crm.book.contact.write"
OWNER_WRITE = "crm.book.owner.write"

RAW = "+917976603446"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- companies


class TestCreatingACompany:
    async def test_a_writer_opens_an_account(self, client, session, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{COMPANY_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers",
                json={
                    "name": "Vector Freight",
                    "owner_id": str(data["subject_b"]),
                    "domain": "vectorfreight.co",
                    "industry": "Logistics",
                    "size_band": "51-200",
                    "city": "Pune",
                    "country": "India",
                    "status": "lead",
                    "annual_revenue_minor": 45_000_000,
                    "currency": "inr",
                },
            )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Vector Freight"
        assert body["status"] == "lead"
        # Minor units, unchanged, and the currency canonicalised by the service.
        assert body["annual_revenue_minor"] == 45_000_000
        assert body["currency"] == "INR"

        stored = await selectors.get_company(session, org_id, uuid.UUID(body["id"]))
        assert stored is not None
        assert stored.organization_id == org_id
        assert stored.owner_id == data["subject_b"]

    async def test_a_reader_cannot_open_an_account(self, client, session, org_id, data) -> None:
        """Holding company.read but not company.write cannot create — the guard is by name."""
        with as_principal(principal_for(org_id, [f"{COMPANY_READ}:ORG"])):
            response = await client.post(
                "/api/v1/customers",
                json={"name": "Intruder Holdings", "owner_id": str(data["subject_a"])},
            )

        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        assert await selectors.get_company_by_name(session, org_id, "Intruder Holdings") is None

    async def test_an_own_writer_cannot_open_an_account_for_another_rep(
        self, client, session, org_id, data
    ) -> None:
        """Scope binds on create as well as on detail. A rep with OWN scope may open the
        accounts they will own; naming a colleague as the owner is an ORG-scoped act, and
        create must not be the way around a check the detail routes already make."""
        own = principal_for(org_id, [f"{COMPANY_WRITE}:OWN"], subject_ref=data["subject_a"])
        with as_principal(own):
            refused = await client.post(
                "/api/v1/customers",
                json={"name": "Handed Over Ltd", "owner_id": str(data["subject_b"])},
            )
            allowed = await client.post(
                "/api/v1/customers",
                json={"name": "Mine Ltd", "owner_id": str(data["subject_a"])},
            )

        assert refused.status_code == 403
        assert allowed.status_code == 201
        assert await selectors.get_company_by_name(session, org_id, "Handed Over Ltd") is None

    async def test_money_with_a_fractional_part_is_refused(self, client, org_id, data) -> None:
        """Minor units are an integer. A float here is the rounding bug the rule exists to
        prevent, so it is refused rather than quietly truncated."""
        with as_principal(principal_for(org_id, [f"{COMPANY_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers",
                json={
                    "name": "Rounding Errors Ltd",
                    "owner_id": str(data["subject_a"]),
                    "annual_revenue_minor": 1250.75,
                },
            )
        assert response.status_code == 422

    async def test_a_status_outside_the_vocabulary_is_refused(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{COMPANY_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers",
                json={
                    "name": "Undefined State Ltd",
                    "owner_id": str(data["subject_a"]),
                    "status": "archived",
                },
            )
        assert response.status_code == 422

    async def test_the_tenant_cannot_be_named_in_the_payload(
        self, client, session, org_id, data
    ) -> None:
        """`organization_id` comes from the caller's context. A payload that carries one is
        refused outright rather than ignored, so the attempt is never ambiguous."""
        with as_principal(principal_for(org_id, [f"{COMPANY_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers",
                json={
                    "name": "Wrong Tenant Ltd",
                    "owner_id": str(data["subject_a"]),
                    "organization_id": str(uuid.uuid4()),
                },
            )
        assert response.status_code == 422
        assert await selectors.get_company_by_name(session, org_id, "Wrong Tenant Ltd") is None

    async def test_a_duplicate_name_is_a_conflict(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{COMPANY_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers",
                json={"name": "Northwind Logistics", "owner_id": str(data["subject_a"])},
            )
        assert response.status_code == 409

    async def test_the_route_stages_the_audit_row_and_the_event(
        self, client, session, org_id, data
    ) -> None:
        """The HTTP path produces exactly what the seed path does, and the audit row carries
        the request it was made from — which is what `request=request` on the route buys."""
        with as_principal(principal_for(org_id, [f"{COMPANY_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers",
                json={"name": "Audited Over HTTP Ltd", "owner_id": str(data["subject_a"])},
            )
        assert response.status_code == 201
        company_id = uuid.UUID(response.json()["id"])

        audit = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "company.created", AuditLog.entity_id == company_id
                    )
                )
            )
            .scalars()
            .one()
        )
        assert audit.actor_id is not None
        assert audit.request_id == response.headers["X-Request-ID"]

        event = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == company_id)
                )
            )
            .scalars()
            .one()
        )
        assert event.event_type == "crm.book.company.created"
        # Staged, not published inline — the relay is the only publisher.
        assert event.published_at is None


# --------------------------------------------------------------------- contacts


class TestCreatingAContact:
    def _payload(self, data: dict, **overrides: object) -> dict:
        payload: dict = {
            "full_name": "Anjali Deshpande",
            "email": "anjali.deshpande@northwind.co",
            "company_id": str(data["company_a_id"]),
            "owner_id": str(data["subject_a"]),
            "title": "Head of Procurement",
            "status": "qualified",
        }
        payload.update(overrides)
        return payload

    async def test_a_writer_adds_a_person_to_an_account(
        self, client, session, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post("/api/v1/contacts", json=self._payload(data))

        assert response.status_code == 201
        body = response.json()
        assert body["full_name"] == "Anjali Deshpande"
        assert body["status"] == "qualified"
        assert body["company_id"] == str(data["company_a_id"])
        # Never touched, and not the client's to claim.
        assert body["last_activity_at"] is None
        assert body["phone"] == ""

        stored = await selectors.get_contact(session, org_id, uuid.UUID(body["id"]))
        assert stored is not None
        assert stored.company_id == data["company_a_id"]
        assert stored.phone_enc is None

    async def test_a_reader_cannot_add_a_contact(self, client, session, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_READ}:ORG"])):
            response = await client.post("/api/v1/contacts", json=self._payload(data))

        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        found = await selectors.get_contact_by_email(
            session, org_id, "anjali.deshpande@northwind.co"
        )
        assert found is None

    async def test_an_own_writer_cannot_add_a_contact_for_another_rep(
        self, client, session, org_id, data
    ) -> None:
        own = principal_for(org_id, [f"{CONTACT_WRITE}:OWN"], subject_ref=data["subject_a"])
        with as_principal(own):
            response = await client.post(
                "/api/v1/contacts",
                json=self._payload(data, owner_id=str(data["subject_b"])),
            )
        assert response.status_code == 403
        found = await selectors.get_contact_by_email(
            session, org_id, "anjali.deshpande@northwind.co"
        )
        assert found is None

    async def test_a_supplied_number_is_sealed_and_its_mask_derived(
        self, client, session, org_id, data
    ) -> None:
        """The one way a real number enters on create. It is sealed, the mask is derived from
        it, and the response is the ordinary masked contact — so create is not a read-back
        oracle for the number the caller just sent."""
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/contacts", json=self._payload(data, raw_phone=RAW)
            )

        assert response.status_code == 201
        body = response.json()
        assert body["phone"].endswith("3446")
        assert BULLET in body["phone"]
        assert RAW not in response.text
        assert "7976603446" not in response.text

        stored = await selectors.get_contact(session, org_id, uuid.UUID(body["id"]))
        assert stored is not None
        assert stored.phone_enc is not None
        assert RAW.encode() not in stored.phone_enc
        # Written beside the sealed value, so an inbound message can find this contact.
        assert stored.phone_index is not None

    async def test_a_number_without_a_country_code_never_reaches_the_column(
        self, client, session, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/contacts", json=self._payload(data, raw_phone="7976603446")
            )

        assert response.status_code == 422
        # The whole create is refused, not just the number: the transaction rolled back.
        found = await selectors.get_contact_by_email(
            session, org_id, "anjali.deshpande@northwind.co"
        )
        assert found is None

    async def test_a_mask_cannot_be_supplied_by_a_client(self, client, org_id, data) -> None:
        """A mask a client sent is a redaction of nothing. The public surface takes the real
        number and derives the mask, or takes neither."""
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/contacts",
                json=self._payload(data, phone=f"+91 98{BULLET}{BULLET} 3446"),
            )
        assert response.status_code == 422

    async def test_a_company_in_another_organization_reads_as_not_found(
        self, client, org_id, data
    ) -> None:
        """Not a constraint error: confirming an id exists in some other tenant is itself a
        disclosure."""
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/contacts", json=self._payload(data, company_id=str(uuid.uuid4()))
            )
        assert response.status_code == 404

    async def test_a_company_status_is_not_a_contact_status(self, client, org_id, data) -> None:
        """The two vocabularies overlap but are not the same — "prospect" is an account's
        relationship to the tenant, never a person's funnel position."""
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/contacts", json=self._payload(data, status="prospect")
            )
        assert response.status_code == 422


# --------------------------------------------------------------------- the rep directory


class TestCreatingAnOwner:
    async def test_an_org_scoped_writer_adds_a_rep(self, client, session, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{OWNER_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers/owners",
                json={
                    "full_name": "Ananya Rao",
                    "email": "ananya.rao@opsgrids.example.com",
                    "role": "Enterprise AE",
                },
            )

        assert response.status_code == 201
        body = response.json()
        assert body["full_name"] == "Ananya Rao"
        assert body["role"] == "Enterprise AE"

        stored = await selectors.get_owner_by_email(
            session, org_id, "ananya.rao@opsgrids.example.com"
        )
        assert stored is not None
        assert str(stored.id) == body["id"]
        assert stored.organization_id == org_id

    async def test_a_new_rep_resolves_in_the_directory(self, client, org_id, data) -> None:
        """The directory is what makes an `owner_id` legible anywhere in the product, so a rep
        added here has to appear in the list the SPA resolves names from."""
        with as_principal(principal_for(org_id, [f"{OWNER_WRITE}:ORG"])):
            created = await client.post(
                "/api/v1/customers/owners",
                json={"full_name": "Ananya Rao", "email": "ananya.rao@opsgrids.example.com"},
            )
        with as_principal(principal_for(org_id, [f"{COMPANY_READ}:OWN"])):
            listed = await client.get("/api/v1/customers/owners")

        assert created.status_code == 201
        assert listed.status_code == 200
        assert "Ananya Rao" in {rep["full_name"] for rep in listed.json()}

    async def test_the_company_permission_does_not_open_the_directory(
        self, client, session, org_id, data
    ) -> None:
        """A rep who maintains their own accounts has no business extending the directory
        those accounts point at. Riding this write on company.write would have handed every
        CRM seat that power the moment the route existed."""
        seat = principal_for(
            org_id,
            [f"{COMPANY_WRITE}:ORG", f"{COMPANY_READ}:ORG", f"{CONTACT_WRITE}:ORG"],
            subject_ref=data["subject_a"],
        )
        with as_principal(seat):
            response = await client.post(
                "/api/v1/customers/owners",
                json={"full_name": "Smuggled In", "email": "smuggled@opsgrids.example.com"},
            )

        assert response.status_code == 403
        found = await selectors.get_owner_by_email(session, org_id, "smuggled@opsgrids.example.com")
        assert found is None

    async def test_a_narrower_grant_does_not_reach_the_floor(
        self, client, session, org_id, data
    ) -> None:
        """Holding the permission is not holding enough scope. The directory is reference data
        for the whole organization — there is no rep who owns the list of reps — so the route
        demands ORG and an OWN grant is refused rather than silently widened."""
        with as_principal(
            principal_for(org_id, [f"{OWNER_WRITE}:OWN"], subject_ref=data["subject_a"])
        ):
            response = await client.post(
                "/api/v1/customers/owners",
                json={"full_name": "Narrow Grant", "email": "narrow@opsgrids.example.com"},
            )

        assert response.status_code == 403
        found = await selectors.get_owner_by_email(session, org_id, "narrow@opsgrids.example.com")
        assert found is None

    async def test_a_duplicate_email_is_a_conflict(self, client, org_id, data) -> None:
        """Email is the directory's per-organization natural key — what makes the demo seed
        idempotent — so a second rep claiming one is refused, not inserted."""
        with as_principal(principal_for(org_id, [f"{OWNER_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers/owners",
                json={
                    "full_name": "Not Sofia",
                    "email": "sofia.marchetti@opsgrids.example.com",
                },
            )
        assert response.status_code == 409

    async def test_a_malformed_email_is_refused(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{OWNER_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers/owners",
                json={"full_name": "No Address", "email": "not-an-email"},
            )
        assert response.status_code == 422

    async def test_the_row_id_is_not_the_clients_to_choose(self, client, org_id, data) -> None:
        """The demo seed derives deterministic ids so deal-service's rows cross-link; that is a
        seeding affordance, not part of the contract."""
        with as_principal(principal_for(org_id, [f"{OWNER_WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/customers/owners",
                json={
                    "full_name": "Chosen Id",
                    "email": "chosen.id@opsgrids.example.com",
                    "owner_id": str(uuid.uuid4()),
                },
            )
        assert response.status_code == 422
