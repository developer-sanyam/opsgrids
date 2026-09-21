"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a *list*,
`ctx.assert_can_touch` guards a single *object* — because filtering the list and forgetting the
detail is the named vulnerability. A user who can list only their own incidents must also be
refused another's by number.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.schemas import IncidentIn
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _numbers(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        rows = (await session.execute(selectors.incidents_stmt(ctx))).scalars().all()
        return {i.number for i in rows}

    async def test_org_scope_sees_every_incident(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "itsm.service.incident.read", Scope.ORG)
        assert await self._numbers(session, ctx) == {"INC-4821", "INC-4812"}

    async def test_own_scope_sees_only_incidents_you_raised(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "itsm.service.incident.read", Scope.OWN, subject_ref=data["subject_a"]
        )
        assert await self._numbers(session, ctx) == {"INC-4821"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(org_id, "itsm.service.incident.read", Scope.OWN, subject_ref=None)
        assert await self._numbers(session, ctx) == set()

    async def test_a_search_cannot_widen_scope(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "itsm.service.incident.read", Scope.OWN, subject_ref=data["subject_a"]
        )
        stmt = selectors.incidents_stmt(ctx, q="failover")  # matches only INC-4812 (subject_b)
        assert (await session.execute(stmt)).all() == []


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(self, client, org_id, data) -> None:
        """A caller with OWN scope is refused another's incident by number, but not their own."""
        principal = principal_for(
            org_id, ["itsm.service.incident.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            refused = await client.get("/api/v1/itsm/incidents/INC-4812")  # subject_b's
            allowed = await client.get("/api/v1/itsm/incidents/INC-4821")  # subject_a's
        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["id"] == "INC-4821"


# --------------------------------------------------------------------- endpoint authz


READ_ENDPOINTS = [
    "/api/v1/itsm/incidents",
    "/api/v1/itsm/service-requests",
    "/api/v1/itsm/catalog",
    "/api/v1/itsm/assets",
    "/api/v1/itsm/overview",
]


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        for path in READ_ENDPOINTS:
            response = await client.get(path)
            assert response.status_code == 401, path

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to ITSM, but holding no ITSM permission at all."""
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            for path in READ_ENDPOINTS:
                response = await client.get(path)
                assert response.status_code == 403, path
                assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["itsm.service.incident.read:ORG", "itsm.cmdb.asset.read:ORG"],
            subject_ref=data["subject_a"],
            entitlements=[],
        )
        with as_principal(principal):
            incidents = await client.get("/api/v1/itsm/incidents")
            assets = await client.get("/api/v1/itsm/assets")
        assert incidents.status_code == 402
        assert assets.status_code == 402

    async def test_a_reader_cannot_create_an_asset(self, client, org_id, data) -> None:
        """Holding asset.read but not asset.write cannot create — the write guard is by name."""
        principal = principal_for(
            org_id, ["itsm.cmdb.asset.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/assets",
                json={"tag": "AST-9999", "name": "Intruder laptop", "type": "laptop"},
            )
        assert response.status_code == 403

    async def test_a_reader_cannot_raise_an_incident(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["itsm.service.incident.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents",
                json={"summary": "Printer jam", "requester": "Nobody"},
            )
        assert response.status_code == 403

    async def test_a_writer_may_raise_an_incident(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["itsm.service.incident.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents",
                json={
                    "summary": "New laptop won't power on",
                    "priority": "P2",
                    "category": "hardware",
                    "requester": "Lucas Almeida",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "new"
        assert body["id"].startswith("INC-")
        assert body["sla_state"] == "on_track"
        assert len(body["worklog"]) == 1

    async def test_an_own_reader_sees_only_their_own_incidents_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["itsm.service.incident.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/itsm/incidents")
        assert response.status_code == 200
        numbers = {item["id"] for item in response.json()["items"]}
        assert numbers == {"INC-4821"}

    # ---------------------------------------------------------------- the catalog

    async def test_request_write_does_not_carry_the_catalog(self, client, org_id, data) -> None:
        """The catalog is *read* with `request.read`, but writing it is guarded by its own name:
        raising requests must not let a seat-holder change what the organization offers."""
        principal = principal_for(
            org_id,
            ["itsm.service.request.read:ORG", "itsm.service.request.write:ORG"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/catalog",
                json={"code": "CAT-99", "name": "Rogue offering", "category": "access"},
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_an_own_grant_cannot_write_the_catalog(self, client, org_id, data) -> None:
        """Catalog write floors at ORG — there is no offering a person owns."""
        principal = principal_for(
            org_id, ["itsm.service.catalog.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/catalog",
                json={"code": "CAT-98", "name": "Narrow offering", "category": "access"},
            )
        assert response.status_code == 403

    async def test_a_catalog_writer_may_add_an_offering(
        self, client, session, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["itsm.service.catalog.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/catalog",
                json={
                    "code": "CAT-09",
                    "name": "Replacement laptop",
                    "category": "hardware",
                    "description": "Swap a failed machine.",
                    "sla_days": 2,
                    # The trailing rollup is the server's to count; sending it changes nothing.
                    "requests_30d": 999,
                },
            )
        assert response.status_code == 201
        assert response.json() == {
            "id": "CAT-09",
            "name": "Replacement laptop",
            "category": "hardware",
            "description": "Swap a failed machine.",
            "sla_days": 2,
            "requests_30d": 0,
        }
        item = await selectors.get_catalog_item(session, org_id, "CAT-09")
        assert item is not None
        assert (item.name, item.category, item.sla_days) == ("Replacement laptop", "hardware", 2)

    async def test_a_duplicate_catalog_code_is_a_conflict(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["itsm.service.catalog.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/catalog",
                json={"code": "CAT-01", "name": "Second onboarding", "category": "onboarding"},
            )
        assert response.status_code == 409

    # ---------------------------------------------------------------- service requests

    async def test_a_reader_cannot_raise_a_service_request(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["itsm.service.request.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/service-requests",
                json={
                    "number": "REQ-3001",
                    "catalog_item_code": "CAT-01",
                    "requester": "Nobody",
                    "subject": "Nobody at all",
                },
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_a_writer_may_raise_a_service_request(
        self, client, session, org_id, data
    ) -> None:
        """OWN write is enough to raise your own request. The category is denormalised from the
        catalog item, and the request starts awaiting approval whatever the payload claims."""
        principal = principal_for(
            org_id, ["itsm.service.request.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/service-requests",
                json={
                    "number": "REQ-3001",
                    "catalog_item_code": "CAT-01",
                    "requester": "Sneha Kulkarni",
                    "subject": "Kiran Rao (joins 14 Oct)",
                    "assignee": "Meera Raghavan",
                    # Neither of these is part of the contract; both must be ignored.
                    "category": "access",
                    "status": "fulfilled",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "REQ-3001"
        assert body["status"] == "approval"
        assert body["category"] == "onboarding"
        assert body["catalog_item_name"] == "New joiner onboarding"

        raised = await selectors.get_request(session, org_id, "REQ-3001")
        assert raised is not None
        # The caller's directory id is stamped as the requester — what OWN scope reads back.
        assert raised.requester_id == data["subject_a"]
        assert raised.status == "approval"

    async def test_a_request_against_an_unknown_item_is_not_found_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["itsm.service.request.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/service-requests",
                json={
                    "number": "REQ-3002",
                    "catalog_item_code": "CAT-NOPE",
                    "requester": "Sneha Kulkarni",
                    "subject": "Something unoffered",
                },
            )
        assert response.status_code == 404


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_raising_against_an_unknown_asset_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="asset"):
            async with session.begin():
                await services.raise_incident(
                    session,
                    ctx,
                    IncidentIn(summary="x", requester="y", affected_asset_tag="AST-DOESNOTEXIST"),
                )

    async def test_a_duplicate_incident_number_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_incident(
                    session, ctx, number="INC-4821", summary="clash", requester="z"
                )

    async def test_a_request_against_an_unknown_catalog_item_is_refused(
        self, session, org_id, data
    ) -> None:
        ctx = ctx_for(org_id, "itsm.service.request.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="catalog"):
            async with session.begin():
                await services.create_request(
                    session,
                    ctx,
                    number="REQ-9999",
                    catalog_item_code="CAT-NOPE",
                    requester="a",
                    subject="b",
                )

    async def test_raising_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data
    ) -> None:
        """The change, its audit row and its event commit together — nothing publishes inline."""
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        async with session.begin():
            inc = await services.create_incident(
                session, ctx, number="INC-5000", summary="Audited", requester="q"
            )
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "incident.raised", AuditLog.entity_id == inc.id)
            )
        ).scalar_one()
        events = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == inc.id)))
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1
        assert events[0].event_type == "itsm.service.incident.raised"
        assert events[0].published_at is None


class TestWritePayloadValidation:
    """A malformed payload is refused by the contract, before it reaches the service layer."""

    async def test_an_unknown_catalog_category_is_unprocessable(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["itsm.service.catalog.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/catalog",
                json={"code": "CAT-10", "name": "Odd one", "category": "teleportation"},
            )
        assert response.status_code == 422

    async def test_a_catalog_item_needs_a_target_of_at_least_a_day(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["itsm.service.catalog.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/catalog",
                json={
                    "code": "CAT-11",
                    "name": "Instant everything",
                    "category": "access",
                    "sla_days": 0,
                },
            )
        assert response.status_code == 422

    async def test_a_service_request_without_a_subject_is_unprocessable(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["itsm.service.request.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/service-requests",
                json={
                    "number": "REQ-3003",
                    "catalog_item_code": "CAT-01",
                    "requester": "Sneha Kulkarni",
                },
            )
        assert response.status_code == 422
