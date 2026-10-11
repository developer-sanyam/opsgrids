"""Every `crm.pipeline.*` event, built through the real function that publishes it and validated
against the JSON Schema committed for it under `libs/contracts/events/`.

The static half (every schema is valid JSON Schema, one exists for every `crm.*` event a service
declares) is `libs/contracts/tests/test_event_schemas.py`. The deeper half lives here, because
checking a real *payload* needs a transaction to stage one through: the payload is read back from
the outbox row the service wrote, the same row the relay would publish, never a hand-written dict.

- `test_deal_*_matches_its_schema` and `TestActivityLogged`: the schema and the producer agree.
  The three stage events were committed ahead of their producer until R2.3 closed that gap, so
  `AHEAD_OF_PRODUCER` below is empty; a new gap gets a row here and its name in
  `PINNED_AHEAD_OF_PRODUCER` in `libs/contracts/tests/test_event_schemas.py`. The deeper check of
  the three stage events, in every pipeline state, is `test_deal_events_contract.py`.

`date-time` is not machine-checked in this environment (`rfc3339-validator` is not installed, so
`validate_event_schema` accepts any string for it): the activity test asserts the offset by hand.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import DealPatch
from platform_core.auth import Scope
from platform_core.events import event_schema_path, validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `test_event_contracts.py`, `tests/`, `deal`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: Event type -> (required properties the producer does not emit yet, the release that closes it).
AHEAD_OF_PRODUCER: dict[str, tuple[frozenset[str], str]] = {}


def _schema(event_type: str) -> dict[str, Any]:
    path = event_schema_path(event_type, 1, contracts_root=CONTRACTS_ROOT)
    return dict(json.loads(path.read_text(encoding="utf-8")))


async def _staged(session: Any, event_type: str, aggregate_id: uuid.UUID) -> dict[str, Any]:
    """The payload of the one `event_type` event staged for this aggregate, read back the way the
    relay would read it."""
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


def _conforms(event_type: str, payload: dict[str, Any]) -> None:
    validate_event_schema(event_type, 1, payload, contracts_root=CONTRACTS_ROOT)


def _write(org_id: uuid.UUID) -> Any:
    return ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)


async def test_deal_deleted_matches_its_schema(session, org_id, data) -> None:
    async with session.begin():
        await services.delete_deal(session, _write(org_id), data["deal_b"])
    _conforms(
        services.EVT_DEAL_DELETED,
        await _staged(session, services.EVT_DEAL_DELETED, data["deal_b_id"]),
    )


async def test_deal_created_matches_its_schema(session, org_id, data) -> None:
    _conforms(
        services.EVT_DEAL_CREATED,
        await _staged(session, services.EVT_DEAL_CREATED, data["deal_a_id"]),
    )


async def test_deal_updated_matches_its_schema(session, org_id, data) -> None:
    async with session.begin():
        await services.update_deal(
            session, _write(org_id), data["deal_a"], DealPatch(name="Fleet telematics v2")
        )
    _conforms(
        services.EVT_DEAL_UPDATED,
        await _staged(session, services.EVT_DEAL_UPDATED, data["deal_a_id"]),
    )


async def test_deal_stage_changed_matches_its_schema(session, org_id, data) -> None:
    async with session.begin():
        await services.move_stage(session, _write(org_id), data["deal_a"], new_stage="proposal")
    _conforms(
        services.EVT_DEAL_STAGE_CHANGED,
        await _staged(session, services.EVT_DEAL_STAGE_CHANGED, data["deal_a_id"]),
    )


class TestActivityLogged:
    """The one event consumed by another service (customer advances `last_activity_at` from it)
    and the one with no committed schema before R1.2b."""

    async def _log(self, session: Any, org_id: uuid.UUID, **named: Any) -> uuid.UUID:
        async with session.begin():
            activity = await services.log_activity(
                session,
                ctx_for(org_id, "crm.pipeline.activity.write", Scope.ORG),
                activity_type="call",
                subject="Pricing follow-up",
                body="Agreed to a 30-vehicle pilot.",
                **named,
            )
        return activity.id

    async def test_an_entry_naming_only_a_deal_matches_its_schema(
        self, session, org_id, data
    ) -> None:
        activity_id = await self._log(
            session, org_id, owner_id=data["subject_a"], deal_id=data["deal_a_id"]
        )
        payload = await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id)
        _conforms(services.EVT_ACTIVITY_LOGGED, payload)
        assert payload["deal_id"] == str(data["deal_a_id"])
        assert payload["contact_id"] is None

    async def test_an_entry_naming_only_a_contact_matches_its_schema(
        self, session, org_id, data
    ) -> None:
        activity_id = await self._log(
            session, org_id, owner_id=data["subject_a"], contact_id=data["contact_a"]
        )
        payload = await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id)
        _conforms(services.EVT_ACTIVITY_LOGGED, payload)
        assert payload["deal_id"] is None
        assert payload["contact_id"] == str(data["contact_a"])

    async def test_an_entry_naming_both_matches_its_schema(self, session, org_id, data) -> None:
        activity_id = await self._log(
            session,
            org_id,
            owner_id=data["subject_a"],
            deal_id=data["deal_a_id"],
            contact_id=data["contact_a"],
        )
        _conforms(
            services.EVT_ACTIVITY_LOGGED,
            await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id),
        )

    async def test_the_payload_carries_no_subject_or_body(self, session, org_id, data) -> None:
        """Ids and an enumeration only: the schema forbids extra keys, so a subject or a body added
        to the payload fails `_conforms`; this names the rule in the test report as well."""
        activity_id = await self._log(
            session, org_id, owner_id=data["subject_a"], deal_id=data["deal_a_id"]
        )
        payload = await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id)
        assert set(payload) == {
            "organization_id",
            "activity_id",
            "type",
            "deal_id",
            "contact_id",
            "occurred_at",
        }

    async def test_occurred_at_carries_an_offset(self, session, org_id, data) -> None:
        """Checked by hand, because the validator does not check `format: date-time` here."""
        backdated = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)
        for named in ({}, {"occurred_at": backdated}):
            activity_id = await self._log(
                session, org_id, owner_id=data["subject_a"], deal_id=data["deal_a_id"], **named
            )
            payload = await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id)
            published = datetime.fromisoformat(payload["occurred_at"])
            assert published.utcoffset() == timedelta(0), payload["occurred_at"]
            if named:
                assert published == backdated


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
