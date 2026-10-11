"""Every `crm.book.*` event, built through the real function that publishes it and validated
against the JSON Schema committed for it under `libs/contracts/events/`.

The static half (every schema is valid JSON Schema, one exists for every `crm.*` event a service
declares) is `libs/contracts/tests/test_event_schemas.py`. The deeper half lives here, because
checking a real *payload* needs a transaction to stage one through: the payload is read back from
the outbox row the service wrote, the same row the relay would publish, never a hand-written dict.

Two shapes of test, because two shapes of contract exist today:

- `TestConformant`: the schema and the producer agree, so the payload validates as it is.
- `TestAheadOfProducer`: the schema is committed ahead of its producer (its own `$comment` says
  which fields the producer does not emit yet). The test pins exactly what is missing, so the gap
  cannot drift silently and the commit that closes it cannot forget its test. The closing commit
  changes the producer, removes the schema's `$comment`, deletes the row below, deletes the
  event's name from `PINNED_AHEAD_OF_PRODUCER` in `libs/contracts/tests/test_event_schemas.py`,
  and moves the event to `TestConformant`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import CompanyPatch, ContactPatch
from platform_core.auth import Scope
from platform_core.events import event_schema_path, validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `test_event_contracts.py`, `tests/`, `customer`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: Event type -> (required properties the producer does not emit yet, the release that closes it).
AHEAD_OF_PRODUCER: dict[str, tuple[frozenset[str], str]] = {
    services.EVT_COMPANY_CREATED: (frozenset({"created_at"}), "R4.1"),
    services.EVT_COMPANY_UPDATED: (frozenset({"name", "status", "owner_id"}), "R4.1"),
}


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


def _gap(event_type: str, payload: dict[str, Any]) -> tuple[frozenset[str], list[str]]:
    """What the schema requires that the payload lacks, and every *other* thing wrong with it
    (a type or enum error on a field that is emitted). The second list must stay empty."""
    schema = _schema(event_type)
    missing = frozenset(schema["required"]) - payload.keys()
    others = [
        error.message
        for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            payload
        )
        if error.validator != "required"
    ]
    return missing, others


def _assert_pinned(event_type: str, payload: dict[str, Any]) -> None:
    pinned, release = AHEAD_OF_PRODUCER[event_type]
    missing, others = _gap(event_type, payload)
    assert missing == pinned, (
        f"{event_type}: the schema requires {sorted(missing)} that the producer does not emit; "
        f"the pin says {sorted(pinned)} (closes in {release}). If the producer now emits them, "
        "this is the closing commit: remove the schema's $comment, delete the pin row and move the "
        "event to TestConformant."
    )
    assert others == [], f"{event_type}: a field the producer does emit is wrong: {others}"


def _write(org_id: uuid.UUID) -> Any:
    return ctx_for(org_id, "crm.book.company.write", Scope.ORG)


class TestConformant:
    async def test_company_deleted_matches_its_schema(self, session, org_id, data) -> None:
        async with session.begin():
            company = await services.create_company(
                session, _write(org_id), name="Short-lived Co", owner_id=data["subject_a"]
            )
            await services.delete_company(session, _write(org_id), company)
        _conforms(
            services.EVT_COMPANY_DELETED,
            await _staged(session, services.EVT_COMPANY_DELETED, company.id),
        )

    async def test_contact_created_matches_its_schema(self, session, org_id, data) -> None:
        async with session.begin():
            contact = await services.create_contact(
                session,
                _write(org_id),
                full_name="Meera Nair",
                email="meera.nair@northwind.co",
                company_id=data["company_a_id"],
                owner_id=data["subject_a"],
                status="qualified",
            )
        payload = await _staged(session, services.EVT_CONTACT_CREATED, contact.id)
        _conforms(services.EVT_CONTACT_CREATED, payload)
        assert payload["status"] == "qualified"
        assert payload["company_id"] == str(data["company_a_id"])

    async def test_contact_created_by_a_lead_conversion_matches_its_schema(
        self, session, org_id, data
    ) -> None:
        """The same event, from the other writer: a lead's conversion reuses `create_contact`
        (`ensure_company_and_contact_for_lead`), so a tenant automation sees one shape."""
        contact_id = uuid.uuid4()
        async with session.begin():
            _company, contact = await services.ensure_company_and_contact_for_lead(
                session,
                services.lead_conversion_ctx(org_id, None),
                company_id=uuid.uuid4(),
                contact_id=contact_id,
                company_name="Converted Traders",
                owner_id=data["subject_a"],
                contact_full_name="Lee Converted",
                contact_email="lee.converted@converted.example",
                contact_raw_phone=None,
            )
        assert contact is not None
        payload = await _staged(session, services.EVT_CONTACT_CREATED, contact_id)
        _conforms(services.EVT_CONTACT_CREATED, payload)
        assert payload["status"] == "customer"

    async def test_contact_updated_matches_its_schema(self, session, org_id, data) -> None:
        async with session.begin():
            await services.update_contact(
                session, _write(org_id), data["contact_a"], ContactPatch(title="Head of Ops")
            )
        payload = await _staged(session, services.EVT_CONTACT_UPDATED, data["contact_a_id"])
        _conforms(services.EVT_CONTACT_UPDATED, payload)
        assert payload["changed"] == ["title"]

    async def test_contact_deleted_matches_its_schema(self, session, org_id, data) -> None:
        async with session.begin():
            await services.delete_contact(session, _write(org_id), data["contact_b"])
        _conforms(
            services.EVT_CONTACT_DELETED,
            await _staged(session, services.EVT_CONTACT_DELETED, data["contact_b_id"]),
        )


class TestAheadOfProducer:
    async def test_company_created_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        payload = await _staged(session, services.EVT_COMPANY_CREATED, data["company_a_id"])
        _assert_pinned(services.EVT_COMPANY_CREATED, payload)

    async def test_company_updated_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            await services.update_company(
                session, _write(org_id), data["company_a"], CompanyPatch(city="Pune")
            )
        payload = await _staged(session, services.EVT_COMPANY_UPDATED, data["company_a_id"])
        _assert_pinned(services.EVT_COMPANY_UPDATED, payload)


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
