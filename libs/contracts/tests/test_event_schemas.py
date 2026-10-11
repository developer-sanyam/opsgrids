"""Event contract schemas — every JSON Schema under `events/` is valid, named the way
`platform_core.events.build_envelope`'s `schema_uri` expects, and covers every `engage.*` event
notification actually declares.

Deliberately static: nothing here opens a database or imports another service's process state,
which is why it is what CI's "Contracts — events validate against their schemas" job runs
(`.github/workflows/ci.yml`) rather than the tests job. The deeper check — that an event's
*payload*, as the code actually builds it, matches the schema committed for it — needs a real
transaction to stage a real event through, so it lives where that already exists:
`services/notification/tests/test_event_contracts.py`, part of the ordinary sharded backend
test run. This file is what catches a schema that is not even valid JSON Schema, or an event
nobody wrote one for at all, before that test ever gets to run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from platform_core.events import event_schema_path, validate_event_schema

#: `tests`, `contracts`, `libs`, ROOT.
ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "libs" / "contracts"
EVENTS = CONTRACTS / "events"
NOTIFICATION = ROOT / "services" / "notification"
LEAD = ROOT / "services" / "lead"


def _schema_files() -> list[Path]:
    return sorted(EVENTS.glob("*/v*.schema.json"))


def test_every_committed_schema_is_valid_json_schema() -> None:
    files = _schema_files()
    assert files, "no event schemas are committed under libs/contracts/events/"
    for path in files:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def test_every_schema_sits_where_schema_uri_resolves_it() -> None:
    """`event_schema_path` is the one place `schema_uri` is turned into a path — this proves
    every committed file actually sits where a real consumer resolving `schema_uri` would look
    for it, and that its own `$id` agrees with its location."""
    for path in _schema_files():
        event_type = path.parent.name
        version = int(path.name.removesuffix(".schema.json").removeprefix("v"))
        assert event_schema_path(event_type, version, contracts_root=CONTRACTS) == path
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema.get("$id") == f"opsgrids://contracts/{event_type}/v{version}"


def _notification_engage_event_types() -> list[str]:
    """Every `engage.*` event `services.py` actually declares as an `EVT_*` constant, read from
    a fresh interpreter with the service directory as cwd — the same subprocess trick
    `tools/export_openapi.py` uses, and for the same reason: `import app` is ambiguous across
    services, so this cannot be a plain import in a process that might import another one."""
    script = (
        "import app.services as s\n"
        "print('\\n'.join(sorted({\n"
        "    v for k, v in vars(s).items()\n"
        "    if k.startswith('EVT_') and isinstance(v, str) and v.startswith('engage.')\n"
        "})))\n"
    )
    result = subprocess.run(  # noqa: S603 - this interpreter, a literal script, a path we chose
        [sys.executable, "-c", script],
        cwd=NOTIFICATION,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _lead_aborted_event_type() -> str:
    """`crm.lead.conversion_aborted`, read off the constant `services.py` stages it under,
    from a fresh interpreter with lead's own directory as cwd — the same reason and the same
    trick as `_notification_engage_event_types`."""
    result = subprocess.run(
        [sys.executable, "-c", "import app.services as s\nprint(s.EVT_LEAD_CONVERSION_ABORTED)\n"],
        cwd=LEAD,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_the_lead_conversion_abort_event_has_a_committed_schema() -> None:
    """The one `crm.lead.*` event with a committed contract so far — it is the hook a later
    consumer would clean up orphaned records from, so its shape is a promise before anything
    reads it. (`crm.lead.created`/`updated`/`converted` predate this and have none yet.)"""
    event_type = _lead_aborted_event_type()
    assert event_type == "crm.lead.conversion_aborted"
    committed = {path.parent.name for path in _schema_files()}
    assert event_type in committed, (
        f"no committed schema for {event_type} — add "
        "libs/contracts/events/crm.lead.conversion_aborted/v1.schema.json"
    )


_ABORT_EVENT = "crm.lead.conversion_aborted"


def _abort_payload(**overrides: object) -> dict[str, object]:
    """A complete, valid payload for the lead abort event, with `overrides` applied."""
    payload: dict[str, object] = {
        "organization_id": "6f0f4a52-8f6b-4a41-9d8c-1c0b6d7e2a10",
        "lead_id": "0b3c2f40-6c0e-4a39-8a55-3f1d2f8f7a21",
        "reason": "status_changed",
        "generation": 0,
        "claim_id": "c5b1a3f2-9a8e-4d6a-b0c1-2e7f6a5d4c3b",
        "company_id": "5c1d9e0a-2b7f-4f43-8a6e-0d4b7c3a9e11",
        "contact_id": None,
        "deal_id": "9a7e3b52-1c4d-4e68-b2f0-6d8a5c1e7f33",
    }
    payload.update(overrides)
    return payload


def _validate_abort(payload: dict[str, object]) -> None:
    validate_event_schema(_ABORT_EVENT, 1, payload, contracts_root=CONTRACTS)


def test_the_lead_abort_schema_requires_a_generation_and_a_claim_and_names_three_reasons() -> None:
    """A consumer re-derives an aborted attempt's ids from `(lead_id, generation)` and has to be
    able to refuse an event that carries neither, so both are required and the schema is closed.
    `record_deleted` is the third reason: the bump `fail_conversion` makes when customer reports a
    record an earlier attempt created was deleted. Static on purpose: it runs in the contracts
    job without opening a database."""
    schema = json.loads((EVENTS / _ABORT_EVENT / "v1.schema.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert {"generation", "claim_id"} <= set(schema["required"])
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["generation"] == {"type": "integer", "minimum": 0}
    assert schema["properties"]["claim_id"] == {"type": "string", "format": "uuid"}
    assert schema["properties"]["reason"] == {
        "enum": ["status_changed", "owner_changed", "record_deleted"]
    }


def test_a_complete_lead_abort_payload_validates() -> None:
    _validate_abort(_abort_payload())
    _validate_abort(_abort_payload(contact_id="3d2c1b0a-9f8e-4d7c-a6b5-4e3d2c1b0a99", generation=7))


@pytest.mark.parametrize("reason", ["status_changed", "owner_changed", "record_deleted"])
def test_each_of_the_three_abort_reasons_validates(reason: str) -> None:
    _validate_abort(_abort_payload(reason=reason))


def test_an_unknown_abort_reason_is_refused() -> None:
    with pytest.raises(ValidationError):
        _validate_abort(_abort_payload(reason="lead_deleted"))


@pytest.mark.parametrize("missing", ["generation", "claim_id"])
def test_a_lead_abort_payload_without_a_generation_or_a_claim_is_refused(missing: str) -> None:
    """The fail-closed half of the contract: an event from a producer older than R2.1 carries
    neither, and a consumer validating it must see it as invalid rather than act on it."""
    payload = _abort_payload()
    del payload[missing]
    with pytest.raises(ValidationError):
        _validate_abort(payload)


@pytest.mark.parametrize("generation", [-1, 1.5, "0", None])
def test_a_lead_abort_payload_with_a_bad_generation_is_refused(generation: object) -> None:
    with pytest.raises(ValidationError):
        _validate_abort(_abort_payload(generation=generation))


@pytest.mark.parametrize("field", ["claim_id", "lead_id"])
def test_a_lead_abort_payload_with_a_malformed_id_is_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        _validate_abort(_abort_payload(**{field: "not-a-uuid"}))


def test_stock_reserved_rejects_undeclared_fields() -> None:
    """Closed like every other operations inventory schema: a producer adding a field must add
    it to the contract first."""
    path = event_schema_path("operations.inventory.stock.reserved", 1, contracts_root=CONTRACTS)
    validator = Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))
    base = {
        "organization_id": "00000000-0000-0000-0000-000000000000",
        "reference_type": None,
        "reference_id": None,
    }
    assert not list(validator.iter_errors(base))
    assert list(validator.iter_errors({**base, "surprise": 1}))


def test_every_engage_event_notification_emits_has_a_committed_schema() -> None:
    committed = {path.parent.name for path in _schema_files()}
    emitted = _notification_engage_event_types()
    assert emitted, "expected services/notification/app/services.py to declare an engage EVT_*"
    missing = sorted(set(emitted) - committed)
    assert not missing, (
        f"no committed schema for {missing} — add libs/contracts/events/<event_type>/v1.schema.json"
    )
