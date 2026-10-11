"""Writes that bind a deal to a stage, against the organization's pipeline.

`create_deal` and `move_stage` read the stages in their own transaction under the pipeline's
shared lock, so what these tests pin is the contract of R2.3: a deal opens at the pipeline's
**entry stage** unless told otherwise; the stage it is put into must exist and be active (422
`unknown_stage`, 422 `stage_archived` — never a 409); a move into a closing stage is pinned at 100
or 0 (422 `probability_pinned` otherwise); `closed_at` is set once; and a stage move waits for a
stage-configuration transaction rather than landing in a stage it is removing.

Each behaviour is proved on the virtual default **and** on a pipeline of the organization's own
(`discovery`, `scoping`, `quote`, `won`, `lost`), installed directly because no route can create
one before R2.5.

A refused write rolls the session's transaction back, which expires every ORM instance in it; so
these tests keep ids, not objects, and `move_deal` loads the deal inside the transaction that
moves it, the way the route does.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import selectors, services
from app.config import settings
from app.main import app
from app.models import AuditLog, Deal, OutboxEvent
from platform_core.auth import Principal, Scope, get_principal
from platform_core.errors import BusinessRuleProblem, ConflictProblem, ValidationProblem
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for, principal_for
from tests.pipeline_support import (
    COMPANY,
    CUSTOM_STAGES,
    OWNER,
    archived,
    count_rows,
    install_pipeline,
    make_deal,
    move_deal,
    reload_deal,
    tenant_session,
)

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def writer(org_id: uuid.UUID) -> Principal:
    return principal_for(
        org_id,
        ["crm.pipeline.deal.write:ORG", "crm.pipeline.deal.read:ORG"],
        subject_ref=OWNER,
    )


class TestEntryStage:
    async def test_a_deal_opens_at_the_default_pipelines_entry_stage(self, session, org_id) -> None:
        deal = await reload_deal(session, org_id, await make_deal(session, org_id, name="No stage"))
        assert (deal.stage, deal.probability, deal.closed_at) == ("lead", 20, None)

    async def test_a_deal_opens_at_a_custom_pipelines_entry_stage(
        self, session, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        deal = await reload_deal(session, org_id, await make_deal(session, org_id, name="No stage"))
        assert (deal.stage, deal.probability, deal.closed_at) == ("discovery", 10, None)

    async def test_the_entry_stage_skips_an_archived_first_stage(
        self, session, admin_session, org_id
    ) -> None:
        stages = (archived(CUSTOM_STAGES[0]), *CUSTOM_STAGES[1:])
        await install_pipeline(admin_session, org_id, stages)
        deal = await reload_deal(session, org_id, await make_deal(session, org_id, name="Retired"))
        assert deal.stage == "scoping"

    async def test_no_entry_stage_is_422_no_entry_stage_and_writes_nothing(
        self, session, admin_session, org_id
    ) -> None:
        stages = (*(archived(s) for s in CUSTOM_STAGES[:3]), *CUSTOM_STAGES[3:])
        await install_pipeline(admin_session, org_id, stages)
        with pytest.raises(BusinessRuleProblem) as refused:
            await make_deal(session, org_id, name="Nowhere to open")
        assert refused.value.status_code == 422
        assert refused.value.errors is not None
        assert refused.value.errors[0]["code"] == "no_entry_stage"
        assert await count_rows(session, Deal) == 0
        assert await count_rows(session, AuditLog) == 0
        assert await count_rows(session, OutboxEvent) == 0


class TestTargetStages:
    async def test_creating_in_an_unknown_stage_is_422_unknown_stage_on_field_stage(
        self, session, org_id
    ) -> None:
        with pytest.raises(ValidationProblem) as refused:
            await make_deal(session, org_id, name="Galaxy brain", stage="galaxy_brain")
        assert refused.value.status_code == 422
        assert refused.value.errors is not None
        assert refused.value.errors[0]["field"] == "stage"
        assert refused.value.errors[0]["code"] == "unknown_stage"
        assert await count_rows(session, Deal) == 0

    async def test_a_legacy_key_is_unknown_in_a_custom_pipeline(
        self, session, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        with pytest.raises(ValidationProblem) as refused:
            await make_deal(session, org_id, name="Old habits", stage="lead")
        assert refused.value.errors is not None
        assert refused.value.errors[0]["code"] == "unknown_stage"

    async def test_creating_in_an_archived_stage_is_422_stage_archived(
        self, session, admin_session, org_id
    ) -> None:
        stages = (CUSTOM_STAGES[0], archived(CUSTOM_STAGES[1]), *CUSTOM_STAGES[2:])
        await install_pipeline(admin_session, org_id, stages)
        with pytest.raises(ValidationProblem) as refused:
            await make_deal(session, org_id, name="Retired stage", stage="scoping")
        assert refused.value.errors is not None
        assert refused.value.errors[0]["field"] == "stage"
        assert refused.value.errors[0]["code"] == "stage_archived"

    async def test_a_move_into_an_archived_stage_is_422_and_leaves_the_deal_alone(
        self, session, admin_session, org_id
    ) -> None:
        stages = (CUSTOM_STAGES[0], archived(CUSTOM_STAGES[1]), *CUSTOM_STAGES[2:])
        await install_pipeline(admin_session, org_id, stages)
        deal_id = await make_deal(session, org_id, name="Stays put", stage="discovery")
        with pytest.raises(ValidationProblem) as refused:
            await move_deal(session, org_id, deal_id, new_stage="scoping")
        assert refused.value.errors is not None
        assert refused.value.errors[0]["code"] == "stage_archived"
        assert (await reload_deal(session, org_id, deal_id)).stage == "discovery"

    async def test_a_move_into_an_unknown_stage_is_422_not_the_409_of_an_illegal_move(
        self, session, org_id
    ) -> None:
        deal_id = await make_deal(session, org_id, name="Going nowhere")
        with pytest.raises(ValidationProblem) as refused:
            await move_deal(session, org_id, deal_id, new_stage="galaxy_brain")
        assert refused.value.errors is not None
        assert refused.value.errors[0]["code"] == "unknown_stage"


class TestClosing:
    @pytest.mark.parametrize(("target", "wrong"), [("closed_won", 37), ("closed_lost", 10)])
    async def test_closing_with_a_wrong_probability_is_422_probability_pinned(
        self, session, org_id, target: str, wrong: int
    ) -> None:
        deal_id = await make_deal(session, org_id, name="Pinned")
        with pytest.raises(ValidationProblem) as refused:
            await move_deal(session, org_id, deal_id, new_stage=target, probability=wrong)
        assert refused.value.errors is not None
        assert refused.value.errors[0]["field"] == "probability"
        assert refused.value.errors[0]["code"] == "probability_pinned"
        fresh = await reload_deal(session, org_id, deal_id)
        assert (fresh.stage, fresh.probability, fresh.closed_at) == ("lead", 20, None)

    @pytest.mark.parametrize(
        ("target", "supplied", "pin"),
        [
            ("closed_won", None, 100),
            ("closed_won", 100, 100),
            ("closed_lost", None, 0),
            ("closed_lost", 0, 0),
        ],
    )
    async def test_closing_with_the_pin_or_none_succeeds_and_pins(
        self, session, org_id, target: str, supplied: int | None, pin: int
    ) -> None:
        deal_id = await make_deal(session, org_id, name="Closing")
        await move_deal(session, org_id, deal_id, new_stage=target, probability=supplied)
        fresh = await reload_deal(session, org_id, deal_id)
        assert (fresh.stage, fresh.probability) == (target, pin)
        assert fresh.closed_at is not None

    async def test_a_move_into_an_open_stage_takes_the_supplied_or_the_default_probability(
        self, session, org_id
    ) -> None:
        deal_id = await make_deal(session, org_id, name="Moving")
        await move_deal(session, org_id, deal_id, new_stage="proposal")
        assert (await reload_deal(session, org_id, deal_id)).probability == 55
        await move_deal(session, org_id, deal_id, new_stage="negotiation", probability=63)
        fresh = await reload_deal(session, org_id, deal_id)
        assert (fresh.stage, fresh.probability, fresh.closed_at) == ("negotiation", 63, None)

    async def test_a_custom_pipelines_closing_stages_are_pinned_too(
        self, session, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        won_id = await make_deal(session, org_id, name="Won one")
        lost_id = await make_deal(session, org_id, name="Lost one")
        with pytest.raises(ValidationProblem):
            await move_deal(session, org_id, won_id, new_stage="won", probability=99)
        await move_deal(session, org_id, won_id, new_stage="won")
        await move_deal(session, org_id, lost_id, new_stage="lost")
        assert (await reload_deal(session, org_id, won_id)).probability == 100
        assert (await reload_deal(session, org_id, lost_id)).probability == 0

    async def test_a_closed_deal_moves_nowhere_409(self, session, org_id) -> None:
        closed_id = await make_deal(
            session, org_id, name="Done", stage="closed_won", probability=100
        )
        with pytest.raises(ConflictProblem, match="cannot move"):
            await move_deal(session, org_id, closed_id, new_stage="lead")
        # A move to the stage the deal is already in is a 409 as well, not a silent no-op.
        open_id = await make_deal(session, org_id, name="Still open")
        with pytest.raises(ConflictProblem, match="cannot move"):
            await move_deal(session, org_id, open_id, new_stage="lead")

    async def test_closed_at_is_set_once_and_never_changes(self, session, org_id) -> None:
        deal_id = await make_deal(session, org_id, name="Timed")
        await move_deal(session, org_id, deal_id, new_stage="qualified")
        assert (await reload_deal(session, org_id, deal_id)).closed_at is None
        await move_deal(session, org_id, deal_id, new_stage="closed_won")
        first = (await reload_deal(session, org_id, deal_id)).closed_at
        assert first is not None
        with pytest.raises(ConflictProblem):
            await move_deal(session, org_id, deal_id, new_stage="closed_lost")
        assert (await reload_deal(session, org_id, deal_id)).closed_at == first

    async def test_closed_at_is_set_for_a_deal_created_in_a_closed_stage(
        self, session, org_id
    ) -> None:
        backdated = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
        imported = await make_deal(
            session, org_id, name="Imported", stage="closed_won", created_at=backdated
        )
        seeded = await make_deal(session, org_id, name="Seeded", stage="closed_lost")
        assert (await reload_deal(session, org_id, imported)).closed_at == backdated
        fresh = await reload_deal(session, org_id, seeded)
        assert fresh.closed_at is not None
        assert fresh.probability == 0


class TestAuditAndEvents:
    async def test_the_audit_after_keys_are_unchanged(self, session, org_id) -> None:
        deal_id = await make_deal(session, org_id, name="Audited")
        await move_deal(session, org_id, deal_id, new_stage="negotiation")
        async with session.begin():
            created = (
                await session.execute(select(AuditLog).where(AuditLog.action == "deal.created"))
            ).scalar_one()
            moved = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "deal.stage_changed")
                )
            ).scalar_one()
        assert set(created.after or {}) == {"name", "stage", "value_minor", "currency"}
        assert created.after is not None
        assert created.after["stage"] == "lead"
        assert moved.before == {"stage": "lead"}
        assert moved.after == {"stage": "negotiation", "probability": 70}

    async def test_a_refused_move_stages_nothing(self, session, org_id) -> None:
        deal_id = await make_deal(session, org_id, name="Refused")
        before = (await count_rows(session, AuditLog), await count_rows(session, OutboxEvent))
        with pytest.raises(ValidationProblem):
            await move_deal(session, org_id, deal_id, new_stage="closed_won", probability=37)
        with pytest.raises(ValidationProblem):
            await move_deal(session, org_id, deal_id, new_stage="galaxy_brain")
        assert (
            await count_rows(session, AuditLog),
            await count_rows(session, OutboxEvent),
        ) == before


class TestOverHttp:
    async def test_create_with_no_stage_is_201_at_the_entry_stage(self, client, org_id) -> None:
        with as_principal(writer(org_id)):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Defaulted",
                    "company_id": str(COMPANY),
                    "value_minor": 100,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 201, response.text
        assert response.json()["stage"] == "lead"
        assert response.json()["probability"] == 20

    async def test_create_with_no_stage_in_a_custom_pipeline_is_201_at_its_entry_stage(
        self, client, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        with as_principal(writer(org_id)):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Defaulted",
                    "company_id": str(COMPANY),
                    "value_minor": 100,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 201, response.text
        assert response.json()["stage"] == "discovery"
        assert response.json()["probability"] == 10

    async def test_an_unknown_stage_key_is_422_with_the_field_error(
        self, client, session, org_id
    ) -> None:
        with as_principal(writer(org_id)):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Galaxy brain",
                    "company_id": str(COMPANY),
                    "value_minor": 100,
                    "close_date": "2026-12-01",
                    "stage": "galaxy_brain",
                },
            )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        error = response.json()["errors"][0]
        assert (error["field"], error["code"]) == ("stage", "unknown_stage")
        assert await count_rows(session, Deal) == 0

    async def test_a_malformed_stage_key_is_422_from_the_schema(
        self, client, session, org_id
    ) -> None:
        with as_principal(writer(org_id)):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Shouting",
                    "company_id": str(COMPANY),
                    "value_minor": 100,
                    "close_date": "2026-12-01",
                    "stage": "Has Caps",
                },
            )
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "stage"
        assert await count_rows(session, Deal) == 0

    async def test_moving_into_an_archived_stage_is_422_stage_archived(
        self, client, session, admin_session, org_id
    ) -> None:
        stages = (CUSTOM_STAGES[0], archived(CUSTOM_STAGES[1]), *CUSTOM_STAGES[2:])
        await install_pipeline(admin_session, org_id, stages)
        deal_id = await make_deal(session, org_id, name="Archived target", stage="discovery")
        with as_principal(writer(org_id)):
            response = await client.post(
                f"/api/v1/deals/{deal_id}/stage", json={"stage": "scoping"}
            )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "stage_archived"

    async def test_closing_with_a_wrong_probability_is_422_probability_pinned(
        self, client, session, org_id
    ) -> None:
        deal_id = await make_deal(session, org_id, name="Over the wire")
        with as_principal(writer(org_id)):
            response = await client.post(
                f"/api/v1/deals/{deal_id}/stage",
                json={"stage": "closed_won", "probability": 37},
            )
        assert response.status_code == 422
        error = response.json()["errors"][0]
        assert (error["field"], error["code"]) == ("probability", "probability_pinned")
        assert (await reload_deal(session, org_id, deal_id)).stage == "lead"

    async def test_closing_with_no_probability_succeeds_and_pins(
        self, client, session, org_id
    ) -> None:
        deal_id = await make_deal(session, org_id, name="Over the wire")
        with as_principal(writer(org_id)):
            response = await client.post(
                f"/api/v1/deals/{deal_id}/stage", json={"stage": "closed_won"}
            )
        assert response.status_code == 200, response.text
        assert (response.json()["stage"], response.json()["probability"]) == ("closed_won", 100)

    async def test_a_closed_deal_moves_nowhere_409(self, client, session, org_id) -> None:
        deal_id = await make_deal(session, org_id, name="Done", stage="closed_won", probability=100)
        with as_principal(writer(org_id)):
            response = await client.post(f"/api/v1/deals/{deal_id}/stage", json={"stage": "lead"})
        assert response.status_code == 409


def conversion_token(org_id: uuid.UUID) -> str:
    return mint_service_token(
        settings(), audience="deal", caller="lead", claims={"org": str(org_id)}
    )


def conversion_body(deal_id: uuid.UUID) -> dict:
    return {
        "deal_id": str(deal_id),
        "company_id": str(uuid.uuid4()),
        "contact_id": str(uuid.uuid4()),
        "owner_id": str(uuid.uuid4()),
        "name": f"Converted {deal_id.hex[:8]}",
        "close_date": "2026-12-31",
        "source": "form",
    }


class TestLeadConversion:
    async def test_conversion_opens_at_a_custom_pipelines_entry_stage(
        self, client, session, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        deal_id = uuid.uuid4()
        response = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": conversion_token(org_id)},
            json=conversion_body(deal_id),
        )
        assert response.status_code == 200, response.text
        deal = await reload_deal(session, org_id, deal_id)
        assert (deal.stage, deal.probability, deal.source) == ("discovery", 10, "form")

    async def test_lead_conversion_with_no_entry_stage_answers_422_not_409(
        self, client, session, admin_session, org_id
    ) -> None:
        """Lead's client turns every deal-side 409 into "a deal with that name already exists"
        and retries nothing; a 422 is what it maps to a permanent `ConversionRejected`."""
        stages = (*(archived(s) for s in CUSTOM_STAGES[:3]), *CUSTOM_STAGES[3:])
        await install_pipeline(admin_session, org_id, stages)
        response = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": conversion_token(org_id)},
            json=conversion_body(uuid.uuid4()),
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "no_entry_stage"
        assert await count_rows(session, Deal) == 0


class TestPipelineLocks:
    async def test_a_stage_move_waits_for_an_open_stage_configuration_transaction(
        self, session, org_id
    ) -> None:
        deal_id = await make_deal(session, org_id, name="Waiting")
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with tenant_session(org_id) as other:
            async with session.begin():
                await services.lock_pipeline_exclusive(session, org_id)
                with pytest.raises(DBAPIError, match="lock timeout"):
                    async with other.begin():
                        racing = await selectors.get_deal(other, org_id, deal_id)
                        assert racing is not None
                        await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                        await services.move_stage(other, ctx, racing, new_stage="proposal")
            # The configuration transaction committed: the same move now goes through.
            async with other.begin():
                racing = await selectors.get_deal(other, org_id, deal_id)
                assert racing is not None
                await services.move_stage(other, ctx, racing, new_stage="proposal")
        assert (await reload_deal(session, org_id, deal_id)).stage == "proposal"

    async def test_two_moves_do_not_block_each_other(self, session, org_id) -> None:
        first_id = await make_deal(session, org_id, name="First")
        second_id = await make_deal(session, org_id, name="Second")
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with tenant_session(org_id) as other, session.begin():
            held = await selectors.get_deal(session, org_id, first_id)
            assert held is not None
            await services.move_stage(session, ctx, held, new_stage="proposal")  # lock held
            async with other.begin():
                await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                racing = await selectors.get_deal(other, org_id, second_id)
                assert racing is not None
                await services.move_stage(other, ctx, racing, new_stage="qualified")
        assert (await reload_deal(session, org_id, first_id)).stage == "proposal"
        assert (await reload_deal(session, org_id, second_id)).stage == "qualified"
