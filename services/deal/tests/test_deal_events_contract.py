"""The deal events, built through the real write path, against the schemas committed for them.

`crm.pipeline.deal.created`, `.updated` and `.stage_changed` used to be *ahead of the producer*:
each committed schema required fields the service did not emit. From R2.3 they are not, and the
three now also carry the stage's **outcome**, which is what a consumer tests — `from` and `to`
stay keys and never change meaning. Every payload here is staged by the function the routes call,
in a real transaction, and validated with `platform_core.events.validate_event_schema`, in the
three states an organization's pipeline can be in: the virtual default, the same six stages
stored, and a pipeline of its own.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app import selectors, services
from app.models import OutboxEvent
from app.schemas import DealPatch
from platform_core.auth import Scope
from platform_core.events import event_schema_path, validate_event_schema
from tests.helpers import ctx_for
from tests.pipeline_support import install_pipeline, make_deal, move_deal

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: Per pipeline state: the entry stage, an open stage to move to, a won stage to close in, and the
#: labels of the last two as `to_label` should carry them.
MODES = {
    "virtual": ("lead", "negotiation", "closed_won", "Negotiation", "Closed Won"),
    "materialised": ("lead", "negotiation", "closed_won", "Negotiation", "Closed Won"),
    "custom": ("discovery", "quote", "won", "Quote", "Won"),
}
CREATED = "crm.pipeline.deal.created"
UPDATED = "crm.pipeline.deal.updated"
STAGE_CHANGED = "crm.pipeline.deal.stage_changed"
DELETED = "crm.pipeline.deal.deleted"


@pytest_asyncio.fixture(params=sorted(MODES))
async def mode(request, session, admin_session, org_id) -> tuple[str, str, str, str, str]:
    name = request.param
    if name == "materialised":
        async with session.begin():
            await services.materialise_default_pipeline(session, org_id, None)
    elif name == "custom":
        await install_pipeline(admin_session, org_id)
    return MODES[name]


async def staged(session, deal_id) -> list[OutboxEvent]:
    async with session.begin():
        rows = (
            await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == deal_id)
                .order_by(OutboxEvent.created_at)
            )
        ).scalars()
        return list(rows)


def properties_of(event_type: str) -> set[str]:
    path = event_schema_path(event_type, 1, contracts_root=CONTRACTS_ROOT)
    return set(json.loads(path.read_text(encoding="utf-8"))["properties"])


def valid(event: OutboxEvent) -> None:
    validate_event_schema(
        event.event_type, event.event_version, event.payload, contracts_root=CONTRACTS_ROOT
    )


class TestProducerSide:
    async def test_created_updated_and_stage_changed_match_their_schemas(
        self, session, org_id, mode
    ) -> None:
        entry, middle, closing, middle_label, closing_label = mode
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal_id = await make_deal(session, org_id, name="Contract")
        async with session.begin():
            deal = await selectors.get_deal(session, org_id, deal_id)
            assert deal is not None
            await services.update_deal(
                session, ctx, deal, DealPatch(name="Contract renamed", value_minor=2_000)
            )
        await move_deal(session, org_id, deal_id, new_stage=middle)
        await move_deal(session, org_id, deal_id, new_stage=closing)

        events = await staged(session, deal_id)
        assert [e.event_type for e in events] == [CREATED, UPDATED, STAGE_CHANGED, STAGE_CHANGED]
        for event in events:
            valid(event)

        created, updated, opened, closed = (e.payload for e in events)
        assert (created["stage"], created["stage_outcome"], created["closed_at"]) == (
            entry,
            "open",
            None,
        )
        assert updated["changed"] == ["name", "value_minor"]
        assert (updated["name"], updated["value_minor"]) == ("Contract renamed", 2_000)
        assert (updated["stage"], updated["stage_outcome"]) == (entry, "open")
        assert (opened["from"], opened["to"]) == (entry, middle)
        assert (opened["from_outcome"], opened["to_outcome"]) == ("open", "open")
        assert opened["to_label"] == middle_label
        assert opened["closed_at"] is None
        assert (closed["from"], closed["to"]) == (middle, closing)
        assert (closed["from_outcome"], closed["to_outcome"]) == ("open", "won")
        assert closed["to_label"] == closing_label
        assert closed["probability"] == 100
        assert closed["closed_at"] is not None

    async def test_a_deal_created_in_a_closing_stage_says_so(self, session, org_id, mode) -> None:
        _, _, closing, _, _ = mode
        opened_at = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
        deal_id = await make_deal(
            session, org_id, name="Imported", stage=closing, created_at=opened_at
        )
        (event,) = await staged(session, deal_id)
        valid(event)
        assert event.payload["stage"] == closing
        assert event.payload["stage_outcome"] == "won"
        assert event.payload["closed_at"] == opened_at.isoformat()
        assert event.payload["created_at"] == opened_at.isoformat()

    async def test_events_carry_the_crm_ids(self, session, org_id, mode) -> None:
        _, middle, _, _, _ = mode
        deal_id = await make_deal(session, org_id, name="Addressed")
        await move_deal(session, org_id, deal_id, new_stage=middle)
        async with session.begin():
            deal = await selectors.get_deal(session, org_id, deal_id)
            assert deal is not None
            company_id, contact_id = deal.company_id, deal.primary_contact_id
        for event in await staged(session, deal_id):
            assert event.payload["crm_company_id"] == str(company_id)
            assert event.payload["crm_contact_id"] == (
                str(contact_id) if contact_id is not None else None
            )

    async def test_deleted_is_unchanged(self, session, org_id, mode) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.delete", Scope.ORG)
        deal_id = await make_deal(session, org_id, name="Doomed")
        async with session.begin():
            deal = await selectors.get_deal(session, org_id, deal_id)
            assert deal is not None
            await services.delete_deal(session, ctx, deal)
        events = await staged(session, deal_id)
        assert [e.event_type for e in events] == [CREATED, DELETED]
        valid(events[1])
        assert set(events[1].payload) == {"organization_id", "deal_id"}

    async def test_every_payload_carries_exactly_the_fields_its_schema_declares(
        self, session, org_id, mode
    ) -> None:
        _, middle, _, _, _ = mode
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal_id = await make_deal(session, org_id, name="Complete")
        async with session.begin():
            deal = await selectors.get_deal(session, org_id, deal_id)
            assert deal is not None
            await services.update_deal(session, ctx, deal, DealPatch(source="Referral"))
        await move_deal(session, org_id, deal_id, new_stage=middle)
        events = await staged(session, deal_id)
        for event in events:
            assert set(event.payload) == properties_of(event.event_type), event.event_type

    async def test_the_payloads_carry_no_stage_label_beyond_to_label(
        self, session, org_id, mode
    ) -> None:
        """Events carry ids, keys and enumerations. The one label is the target stage's, on
        `stage_changed`, for display only; nothing else a tenant typed rides an event."""
        _, middle, closing, _, _ = mode
        deal_id = await make_deal(session, org_id, name="Quiet")
        await move_deal(session, org_id, deal_id, new_stage=middle)
        await move_deal(session, org_id, deal_id, new_stage=closing)
        forbidden = {"label", "stage_label", "from_label", "description", "notes", "body"}
        for event in await staged(session, deal_id):
            assert not forbidden & set(event.payload), event.event_type
            has_label = "to_label" in event.payload
            assert has_label == (event.event_type == STAGE_CHANGED), event.event_type
