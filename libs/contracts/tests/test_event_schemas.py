"""Event contract schemas — every JSON Schema under `events/` is valid, named the way
`platform_core.events.build_envelope`'s `schema_uri` expects, and covers every `engage.*` event
notification declares and every `crm.*` event customer, deal and lead declare.

Deliberately static: nothing here opens a database or imports another service's process state,
which is why it is what CI's "Contracts — events validate against their schemas" job runs
(`.github/workflows/ci.yml`) rather than the tests job. The deeper check — that an event's
*payload*, as the code actually builds it, matches the schema committed for it — needs a real
transaction to stage a real event through, so it lives where that already exists:
`services/<service>/tests/test_event_contracts.py` (notification, customer, deal and lead), part
of the ordinary sharded backend test run. This file is what catches a schema that is not even
valid JSON Schema, or an event nobody wrote one for at all, before that test ever gets to run.
"""

from __future__ import annotations

import json
import re
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
CUSTOMER = ROOT / "services" / "customer"
DEAL = ROOT / "services" / "deal"


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
    """The `crm.lead.*` event a later consumer would clean up orphaned records from, so its
    shape is a promise before anything reads it. Its siblings (`created`, `updated`,
    `converted`) have committed schemas too, ahead of their producer: the pinned set further
    down holds that list."""
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


def _declared_crm_events(service: Path) -> list[str]:
    """Every `crm.*` event type a CRM service declares as an `EVT_*` constant in `app.services`,
    read from a fresh interpreter with the service directory as cwd — the trick
    `_notification_engage_event_types` uses, for the same reason."""
    script = (
        "import app.services as s\n"
        "print('\\n'.join(sorted({\n"
        "    v for k, v in vars(s).items()\n"
        "    if k.startswith('EVT_') and isinstance(v, str) and v.startswith('crm.')\n"
        "})))\n"
    )
    result = subprocess.run(  # noqa: S603 - this interpreter, a literal script, a path we chose
        [sys.executable, "-c", script],
        cwd=service,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _notification_subscribable_events() -> list[str]:
    """Notification's `SUBSCRIBABLE_EVENTS`, read from a fresh interpreter in its own directory."""
    script = (
        "import json\n"
        "from app.domain import SUBSCRIBABLE_EVENTS\n"
        "print(json.dumps(list(SUBSCRIBABLE_EVENTS)))\n"
    )
    result = subprocess.run(  # noqa: S603 - this interpreter, a literal script, a path we chose
        [sys.executable, "-c", script],
        cwd=NOTIFICATION,
        check=True,
        capture_output=True,
        text=True,
    )
    return [str(key) for key in json.loads(result.stdout)]


def test_every_crm_event_a_service_declares_has_a_committed_schema() -> None:
    """A sixteenth CRM event cannot be published without its contract: customer, deal and lead
    each declare their event types as `EVT_*` constants, and every one needs a schema."""
    committed = {path.parent.name for path in _schema_files()}
    missing: dict[str, list[str]] = {}
    for service in (CUSTOMER, DEAL, LEAD):
        declared = _declared_crm_events(service)
        assert declared, f"expected services/{service.name}/app/services.py to declare crm.* EVT_*"
        gap = sorted(set(declared) - committed)
        if gap:
            missing[service.name] = gap
    assert not missing, (
        f"no committed schema for {missing} — add "
        "libs/contracts/events/<event_type>/v1.schema.json in the same commit as the producer"
    )


def test_every_crm_subscribable_key_has_a_committed_schema() -> None:
    """A tenant may author a rule or an automation on any key notification lists, and a
    checkout's builder offers a trigger's variables from the committed schema: a key with none
    offers nothing."""
    committed = {path.parent.name for path in _schema_files()}
    keys = [key for key in _notification_subscribable_events() if key.startswith("crm.")]
    assert keys, "expected notification's SUBSCRIBABLE_EVENTS to list crm.* keys"
    missing = sorted(set(keys) - committed)
    assert not missing, (
        f"no committed schema for the subscribable keys {missing} — add "
        "libs/contracts/events/<event_type>/v1.schema.json"
    )


#: The `crm.*` schemas committed ahead of their producer (their `$comment` says which fields the
#: producer does not emit yet), each with the release that closes the gap. A ninth cannot appear,
#: and a closed one leaves this set in the commit that closes it — the same commit deletes its row
#: from the service's own `AHEAD_OF_PRODUCER` pin (`services/<service>/tests/
#: test_event_contracts.py`) and removes the schema's `$comment`. Once the set is empty the test
#: below asserts it is empty.
PINNED_AHEAD_OF_PRODUCER: frozenset[str] = frozenset(
    {
        "crm.book.company.created",  # R4.1
        "crm.book.company.updated",  # R4.1
        "crm.lead.created",  # R3.7
        "crm.lead.updated",  # R3.7
        "crm.lead.converted",  # R3.7
        "crm.pipeline.deal.created",  # R2.3
        "crm.pipeline.deal.updated",  # R2.3
        "crm.pipeline.deal.stage_changed",  # R2.3
    }
)


def test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight() -> None:
    ahead = {
        path.parent.name
        for path in _schema_files()
        if path.parent.name.startswith("crm.")
        and str(json.loads(path.read_text(encoding="utf-8")).get("$comment", "")).startswith(
            "Ahead of the producer"
        )
    }
    assert ahead == PINNED_AHEAD_OF_PRODUCER, (
        f"ahead of their producer but not pinned: {sorted(ahead - PINNED_AHEAD_OF_PRODUCER)}; "
        f"pinned but no longer ahead: {sorted(PINNED_AHEAD_OF_PRODUCER - ahead)}. A gap is closed "
        "by changing the producer, removing the schema's $comment, deleting its row from the "
        "service's AHEAD_OF_PRODUCER and its name above, in one commit."
    )


_EVENT_TYPE_KWARG = re.compile(r"""event_type\s*=\s*["'](crm\.[A-Za-z0-9_.]+)["']""")


def test_no_service_test_names_a_crm_event_that_has_no_committed_schema() -> None:
    """A fixture that invents `crm.deal.won` documents an event nobody publishes, and a rule
    seeded on it proves nothing. Every `event_type="crm...."` a service test writes must be real."""
    committed = {path.parent.name for path in _schema_files()}
    phantom: dict[str, list[str]] = {}
    for path in sorted((ROOT / "services").glob("*/tests/**/*.py")):
        for event_type in _EVENT_TYPE_KWARG.findall(path.read_text(encoding="utf-8")):
            if event_type not in committed:
                phantom.setdefault(event_type, []).append(str(path.relative_to(ROOT)))
    assert not phantom, f"service tests name crm events that no producer publishes: {phantom}"
