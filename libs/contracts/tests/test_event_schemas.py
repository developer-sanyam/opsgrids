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

from jsonschema import Draft202012Validator

from platform_core.events import event_schema_path

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


def test_every_engage_event_notification_emits_has_a_committed_schema() -> None:
    committed = {path.parent.name for path in _schema_files()}
    emitted = _notification_engage_event_types()
    assert emitted, "expected services/notification/app/services.py to declare an engage EVT_*"
    missing = sorted(set(emitted) - committed)
    assert not missing, (
        f"no committed schema for {missing} — add libs/contracts/events/<event_type>/v1.schema.json"
    )


FINANCE = ROOT / "services" / "finance"

#: The finance events whose payloads are a promise to a consumer — the ones a notification rule or a
#: dashboard acts on (the e-Way retry story, C4, and the IMS push-back, C2). `EVT_*` constants
#: outside these prefixes predate contracts and have none yet.
_FINANCE_CONTRACTED_PREFIXES = (
    "finance.compliance.eway_bill.generated",
    "finance.compliance.eway_bill.failed",
    "finance.compliance.eway_bill.retry_scheduled",
    "finance.compliance.eway_bill.generation_requested",
    "finance.compliance.ims_push.",
)


def _finance_event_types() -> list[str]:
    """Every `finance.*` event `services.py` declares as an `EVT_*` constant, read from a fresh
    interpreter with finance's directory as cwd — for the reason `_notification_engage_event_types`
    gives."""
    script = (
        "import app.services as s\n"
        "print('\\n'.join(sorted({\n"
        "    v for k, v in vars(s).items()\n"
        "    if k.startswith('EVT_') and isinstance(v, str) and v.startswith('finance.')\n"
        "})))\n"
    )
    result = subprocess.run(  # noqa: S603 - this interpreter, a literal script, a path we chose
        [sys.executable, "-c", script],
        cwd=FINANCE,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_every_contracted_finance_event_has_a_committed_schema() -> None:
    committed = {path.parent.name for path in _schema_files()}
    emitted = [
        event_type
        for event_type in _finance_event_types()
        if event_type.startswith(_FINANCE_CONTRACTED_PREFIXES)
    ]
    assert emitted, "expected services/finance/app/services.py to declare the e-Way retry events"
    missing = sorted(set(emitted) - committed)
    assert not missing, (
        f"no committed schema for {missing} — add libs/contracts/events/<event_type>/v1.schema.json"
    )


def test_every_committed_finance_schema_is_still_emitted() -> None:
    """The other direction: a schema for an event nothing publishes any more is a contract nobody
    can honour, and it would sit here looking authoritative."""
    emitted = set(_finance_event_types())
    committed = {
        path.parent.name for path in _schema_files() if path.parent.name.startswith("finance.")
    }
    stale = sorted(committed - emitted)
    assert not stale, f"committed schemas for events finance no longer declares: {stale}"
