"""The three `crm.lead.*` events committed ahead of their producer, pinned.

`crm.lead.created`, `.updated` and `.converted` have schemas that require fields `lead-service`
does not emit yet (each schema's `$comment` says which). This file pins exactly what is missing,
built through the real functions, so the gap cannot drift silently and the commit that closes it
(the lead-capture work, R3.7) cannot forget its test: that commit changes the producer, removes
the schema's `$comment`, deletes the row below, deletes the event's name from
`PINNED_AHEAD_OF_PRODUCER` in `libs/contracts/tests/test_event_schemas.py`, and validates the
event with `validate_event_schema`.

The fourth event, `crm.lead.conversion_aborted`, conforms and is already tested against its
producer: `test_lead_lifecycle_races.py` (the two `validate_event_schema` calls at `:269` and
`:292`). It is cited here, not copied.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from app import clients, services
from app.models import OutboxEvent
from app.schemas import LeadPatch
from platform_core.auth import Scope
from platform_core.events import event_schema_path
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `test_event_contracts.py`, `tests/`, `lead`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: Event type -> (required properties the producer does not emit yet, the release that closes it).
AHEAD_OF_PRODUCER: dict[str, tuple[frozenset[str], str]] = {
    services.EVT_LEAD_CREATED: (frozenset({"status"}), "R3.7"),
    services.EVT_LEAD_UPDATED: (
        frozenset({"status", "source", "owner_id", "converted_at"}),
        "R3.7",
    ),
    services.EVT_LEAD_CONVERTED: (frozenset({"owner_id", "converted_at"}), "R3.7"),
}


def _schema(event_type: str) -> dict[str, Any]:
    path = event_schema_path(event_type, 1, contracts_root=CONTRACTS_ROOT)
    return dict(json.loads(path.read_text(encoding="utf-8")))


async def _staged(session: Any, event_type: str, aggregate_id: uuid.UUID) -> dict[str, Any]:
    """The payload of the one `event_type` event staged for this lead, read back the way the relay
    would read it."""
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == event_type,
                        OutboxEvent.aggregate_id == aggregate_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, f"expected one {event_type} for {aggregate_id}, found {len(rows)}"
    return dict(rows[0].payload)


def _assert_pinned(event_type: str, payload: dict[str, Any]) -> None:
    """The schema requires exactly the pinned fields the payload lacks, and nothing a field that
    *is* emitted breaks (a type or enum error is a different defect from a missing field)."""
    pinned, release = AHEAD_OF_PRODUCER[event_type]
    schema = _schema(event_type)
    missing = frozenset(schema["required"]) - payload.keys()
    others = [
        error.message
        for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            payload
        )
        if error.validator != "required"
    ]
    assert missing == pinned, (
        f"{event_type}: the schema requires {sorted(missing)} that the producer does not emit; "
        f"the pin says {sorted(pinned)} (closes in {release}). If the producer now emits them, "
        "this is the closing commit: remove the schema's $comment, delete the pin row and "
        "validate the event with validate_event_schema."
    )
    assert others == [], f"{event_type}: a field the producer does emit is wrong: {others}"


class TestAheadOfProducer:
    async def test_lead_created_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        payload = await _staged(session, services.EVT_LEAD_CREATED, data["lead_a_id"])
        _assert_pinned(services.EVT_LEAD_CREATED, payload)

    async def test_lead_updated_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            await services.update_lead(
                session,
                ctx_for(org_id, "crm.lead.lead.write", Scope.ORG),
                data["lead_b_id"],
                LeadPatch(notes="Called back, asked for a quote."),
            )
        payload = await _staged(session, services.EVT_LEAD_UPDATED, data["lead_b_id"])
        _assert_pinned(services.EVT_LEAD_UPDATED, payload)

    async def test_lead_converted_lacks_exactly_the_pinned_fields(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _company_and_contact(
            settings: Any, organization_id: Any, **kwargs: Any
        ) -> tuple[uuid.UUID, uuid.UUID]:
            return uuid.uuid4(), uuid.uuid4()

        async def _deal(settings: Any, organization_id: Any, **kwargs: Any) -> uuid.UUID:
            return uuid.uuid4()

        monkeypatch.setattr(clients, "ensure_company_and_contact", _company_and_contact)
        monkeypatch.setattr(clients, "ensure_deal", _deal)
        await services.convert_lead(
            session, ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG), data["lead_a_id"]
        )
        payload = await _staged(session, services.EVT_LEAD_CONVERTED, data["lead_a_id"])
        _assert_pinned(services.EVT_LEAD_CONVERTED, payload)


def test_the_ahead_comment_and_the_pin_table_agree() -> None:
    """A schema carries the "Ahead of the producer" comment exactly when its event is pinned, for
    every event this service declares: a new gap must be pinned, and a closed one must lose both."""
    declared = sorted(
        value
        for name, value in vars(services).items()
        if name.startswith("EVT_") and isinstance(value, str)
    )
    assert declared, "expected app.services to declare its EVT_* constants"
    for event_type in declared:
        ahead = str(_schema(event_type).get("$comment", "")).startswith("Ahead of the producer")
        assert ahead == (event_type in AHEAD_OF_PRODUCER), event_type
