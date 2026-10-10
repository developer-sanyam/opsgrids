"""The four `crm.lead.*` routing keys a rule or an automation may now react to (registry EV39).

Until R1.2b notification subscribed to five CRM keys and no lead key, so every `crm.lead.*` event
was published to a broker with no queue bound for it and silently dropped, and a tenant could not
even author a rule on one: `domain.validate_rule` and the automation builder refuse a key that is
not in `SUBSCRIBABLE_EVENTS`. These tests prove the four keys are listed, have contracts, and (end
to end through the real consumer) that a rule or an automation on `crm.lead.converted` messages
the converted contact, once, in its own tenant, and only when consent allows it, and that a contact
with no number (the common case) is a skipped rule or a visible `recipient_missing` failure and
never a message.

What no lead event carries is an address: the only contact id on one is `converted`'s (a contact
that exists), so the contact is resolved through customer's messaging target
(`recipient_contact_path` for a rule, `recipient_kind: "contact_id"` for an automation).

Real PostgreSQL as the app role, like the rest of this service's suite.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import a1_services, consent, consumers, domain, services
from app.a1_models import AutomationRun, AutomationRunStep
from app.config import settings
from app.main import app
from app.models import SCHEMA, Message, OutboxEvent, ProcessedEvent
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.db import dispose_engine, init_engine
from platform_core.events import event_schema_path
from tests.helpers import ctx_for, envelope_for, principal_for

pytestmark = pytest.mark.integration

#: `test_crm_lead_keys.py`, `tests/`, `notification`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

LEAD_KEYS = (
    "crm.lead.conversion_aborted",
    "crm.lead.converted",
    "crm.lead.created",
    "crm.lead.updated",
)
CRM_KEYS = (
    "crm.book.company.created",
    "crm.book.contact.created",
    *LEAD_KEYS,
    "crm.pipeline.activity.logged",
    "crm.pipeline.deal.created",
    "crm.pipeline.deal.stage_changed",
)
CONVERTED = "crm.lead.converted"
CONTACT_ID = "c0ffee00-0000-4000-8000-000000000001"
PHONE = "+917976603446"

T_READ = "notification.delivery.template.read"
T_WRITE = "notification.delivery.template.write"
WRITE = "engage.campaign.campaign.write"
SEND = "engage.campaign.campaign.send"
A1_TABLES = ("a1_run_steps", "a1_runs", "a1_versions", "a1_automations")


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


@pytest_asyncio.fixture(autouse=True)
async def _clean(session: Any) -> AsyncIterator[None]:
    """The shared fixture truncates the older tables; the automation engine's are ours."""
    admin = create_async_engine(
        settings().admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    async with admin.begin() as conn:
        tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in A1_TABLES)
        await conn.execute(text(f"TRUNCATE {tables}"))
    await admin.dispose()
    yield


@pytest_asyncio.fixture
async def worker_engine(session: Any) -> AsyncIterator[None]:
    """The process-wide engine `consumers.fold` opens its per-message transaction on."""
    init_engine(settings())
    yield
    await dispose_engine()


def _converted(org_id: uuid.UUID, contact_id: str | None = CONTACT_ID) -> dict[str, Any]:
    """`crm.lead.converted` as lead-service publishes it today: ids only, no address."""
    return envelope_for(
        org_id,
        CONVERTED,
        {
            "organization_id": str(org_id),
            "lead_id": str(uuid.uuid4()),
            "company_id": str(uuid.uuid4()),
            "contact_id": contact_id,
            "deal_id": str(uuid.uuid4()),
        },
    )


async def _resolver(org: str, contact: str, channel: str) -> str | None:
    """Customer's messaging target, standing in: the contact in the event has this number."""
    assert contact == CONTACT_ID and channel == "whatsapp"
    return PHONE


async def _refusing_resolver(org: str, contact: str, channel: str) -> str | None:
    raise AssertionError("another tenant's event must never ask for a contact's address")


async def _no_number_resolver(org: str, contact: str, channel: str) -> str | None:
    """Customer's messaging target for a contact that never had a number: `phone: null` with a
    200, which the client reads as `None` (`services/customer/app/api/internal.py:159-161`).
    Most contacts are in this state, and a lead that came in with an email only converts to one."""
    assert contact == CONTACT_ID and channel == "whatsapp"
    return None


async def _no_lookup_resolver(org: str, contact: str, channel: str) -> str | None:
    raise AssertionError("a step that reads its path as an address must not look a contact up")


async def _template(session: Any, org_id: uuid.UUID, *, category: str = "utility") -> None:
    """An approved WhatsApp template with no variables, registered at Meta as `category`."""
    async with session.begin():
        await services.upsert_provider_template(
            session,
            ctx_for(org_id, T_WRITE, Scope.ORG),
            code="welcome_wa",
            channel="whatsapp",
            provider_name="welcome_converted_lead",
            language="en_US",
            category=category,
            parameter_format="positional",
            status="approved",
            status_reason=None,
            variables=[],
            preview_text="Welcome aboard.",
        )


async def _rule(session: Any, org_id: uuid.UUID) -> None:
    async with session.begin():
        await services.upsert_rule(
            session,
            ctx_for(org_id, T_WRITE, Scope.ORG),
            code="welcome_converted_lead",
            event_type=CONVERTED,
            channel="whatsapp",
            provider_template_code="welcome_wa",
            recipient_contact_path="contact_id",
        )


def _ctx(org_id: uuid.UUID, *perms: str) -> ScopeContext:
    principal = principal_for(org_id, [f"{p}:ORG" for p in perms], entitlements=["engage"])
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=perms[0])


def _recipe_graph(**config: Any) -> dict[str, Any]:
    """The shape the Engage recipe builds: trigger on a conversion, one WhatsApp send whose
    recipient is the contact id on the event. `config` overrides or adds keys of the send step."""
    send: dict[str, Any] = {
        "channel": "whatsapp",
        "template": {"ref": "welcome_wa"},
        "recipient_path": "payload.contact_id",
        "recipient_kind": "contact_id",
        "variable_map": {},
    }
    send.update(config)
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"event_type": CONVERTED}},
            {"id": "send", "type": "send_message", "config": send},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"from": "t", "to": "send"}, {"from": "send", "to": "end"}],
    }


async def _go_live(session: Any, org_id: uuid.UUID, graph: dict[str, Any]) -> None:
    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        row = await a1_services.create_automation(
            session, ctx, name="Welcome", description=None, graph=graph
        )
        await a1_services.publish(session, ctx, automation_id=row.id)


async def _runs(session: Any, org_id: uuid.UUID) -> list[AutomationRun]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(AutomationRun)
                    .where(AutomationRun.organization_id == org_id)
                    .order_by(AutomationRun.started_at)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )


async def _send_step(session: Any, run_id: uuid.UUID) -> AutomationRunStep:
    async with session.begin():
        steps = (
            (
                await session.execute(
                    select(AutomationRunStep)
                    .where(AutomationRunStep.run_id == run_id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
    return next(step for step in steps if step.type == "send_message")


async def _messages(session: Any, org_id: uuid.UUID) -> list[Message]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.organization_id == org_id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )


async def _count(session: Any, model: Any) -> int:
    async with session.begin():
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


# ------------------------------------------------------------------------------ the keys


def test_the_four_lead_keys_are_subscribable_and_have_schemas() -> None:
    for key in LEAD_KEYS:
        assert key in domain.SUBSCRIBABLE_EVENTS, key
        assert event_schema_path(key, 1, contracts_root=CONTRACTS_ROOT).is_file(), key


def test_the_crm_group_is_exactly_the_nine_expected_keys() -> None:
    """Explicit, never a wildcard, and alphabetical like the rest of the list."""
    crm = [key for key in domain.SUBSCRIBABLE_EVENTS if key.startswith("crm.")]
    assert crm == sorted(CRM_KEYS)
    assert len(crm) == 9


async def test_the_subscribable_events_route_lists_the_lead_keys(client, org_id) -> None:
    with as_principal(principal_for(org_id, [f"{T_READ}:ORG"])):
        response = await client.get("/api/v1/notifications/subscribable-events")
    assert response.status_code == 200
    assert set(LEAD_KEYS) <= set(response.json())


async def test_a_rule_on_an_unlisted_lead_key_is_still_a_422(client, org_id) -> None:
    """`crm.lead.assigned` is a key a later release adds (EV41, R4.2). Until then a rule on it is
    refused at authoring like any other typo, so the list stays the whole of what can fire."""
    body = {
        "code": "r",
        "event_type": "crm.lead.assigned",
        "template_code": "t",
        "channel": "email",
        "recipient_path": "requester_email",
    }
    with as_principal(principal_for(org_id, [f"{T_WRITE}:ORG"])):
        response = await client.put("/api/v1/notifications/rules", json=body)
    assert response.status_code == 422
    # Refused for the event type, not for some other defect of the body.
    assert "is not an event this service subscribes to" in response.text


# ------------------------------------------------------------------------------ rules


async def test_a_rule_on_lead_converted_messages_the_converted_contact(session, org_id) -> None:
    await _template(session, org_id)
    await _rule(session, org_id)
    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=org_id,
            envelope=_converted(org_id),
            resolve_target=_resolver,
        )
    assert len(staged) == 1
    assert staged[0].to_address == PHONE
    assert staged[0].channel == "whatsapp"


async def test_a_rule_on_a_contact_with_no_number_stages_nothing_and_does_not_fail(
    session, org_id
) -> None:
    """The common case, not an edge: the contact exists and has never had a number. The rule is
    skipped with a log line (`notification.rule.contact_has_no_address`) and not raised, so the
    other rules on the event, and the event itself, are untouched."""
    await _template(session, org_id)
    await _rule(session, org_id)
    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=org_id,
            envelope=_converted(org_id),
            resolve_target=_no_number_resolver,
        )
    assert staged == []


async def test_a_redelivered_lead_event_is_folded_once(session, org_id, worker_engine) -> None:
    """Redelivery is normal: the ledger row and the message share one transaction, so the second
    delivery of the same event id changes nothing."""
    await _template(session, org_id)
    await _rule(session, org_id)
    envelope = _converted(org_id)

    await consumers.fold(envelope, _resolver)
    await consumers.fold(envelope, _resolver)

    assert len(await _messages(session, org_id)) == 1
    assert await _count(session, ProcessedEvent) == 1


async def test_every_lead_event_is_folded_and_does_nothing_without_a_rule(
    session, org_id, worker_engine
) -> None:
    """What binding four more keys changes on a tenant that authored nothing: each event is
    recorded as processed and nothing else happens. This is the production effect of the change."""
    for key in LEAD_KEYS:
        await consumers.fold(envelope_for(org_id, key, {"organization_id": str(org_id)}))
    assert await _count(session, ProcessedEvent) == len(LEAD_KEYS)
    assert await _messages(session, org_id) == []


async def test_a_lead_event_never_reaches_another_organizations_rule(
    session, admin_session, org_id, worker_engine
) -> None:
    await _template(session, org_id)
    await _rule(session, org_id)

    # Another tenant's conversion: it binds that tenant's GUC, finds none of this tenant's rules,
    # and never asks anyone for an address.
    await consumers.fold(_converted(uuid.uuid4()), _refusing_resolver)
    async with admin_session.begin():
        total = (
            await admin_session.execute(select(func.count()).select_from(Message))
        ).scalar_one()
    assert total == 0

    # The rule is live: the same kind of event for its own tenant does fire it.
    await consumers.fold(_converted(org_id), _resolver)
    assert len(await _messages(session, org_id)) == 1


# ------------------------------------------------------------------------------ automations


async def test_an_automation_shaped_like_the_recipe_messages_the_converted_contact(
    session, org_id, worker_engine
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _recipe_graph())

    await consumers.fold(_converted(org_id), _resolver)
    (run,) = await _runs(session, org_id)
    assert run.state == "completed"
    (message,) = await _messages(session, org_id)
    assert message.to_address == PHONE and message.state == "pending"

    # A phone-only lead converts with no contact: the step cannot find a recipient and says so.
    await consumers.fold(_converted(org_id, contact_id=None), _resolver)
    failed = [r for r in await _runs(session, org_id) if r.state == "failed"]
    assert len(failed) == 1 and failed[0].failure_code == "recipient_missing"
    assert len(await _messages(session, org_id)) == 1


async def test_a_converted_contact_with_no_number_is_a_visible_failure_and_sends_nothing(
    session, org_id, worker_engine
) -> None:
    """The same case through the recipe's automation. The run ends `failed` with
    `recipient_missing`, which the tenant sees in the run history, and nothing is queued. A
    redelivery of the event changes nothing."""
    await _template(session, org_id)
    await _go_live(session, org_id, _recipe_graph(purpose="marketing"))
    envelope = _converted(org_id)

    await consumers.fold(envelope, _no_number_resolver)
    await consumers.fold(envelope, _no_number_resolver)

    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "recipient_missing"
    assert await _messages(session, org_id) == []


async def test_a_contact_id_read_as_an_address_is_refused_and_nothing_is_sent(
    session, org_id, worker_engine
) -> None:
    """What a person gets who retypes the builder's "Send to" path (Task R1.2.12): the kind is
    dropped, the path still names the contact id, and the id is read as a phone number. It is not
    an E.164 number, so the step fails `recipient_invalid`; no contact is looked up and no one is
    messaged."""
    await _template(session, org_id)
    graph = _recipe_graph(purpose="marketing")
    del graph["nodes"][1]["config"]["recipient_kind"]
    await _go_live(session, org_id, graph)

    await consumers.fold(_converted(org_id), _no_lookup_resolver)

    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "recipient_invalid"
    assert await _messages(session, org_id) == []


# ------------------------------------------------------------------------------ consent


async def test_the_recipe_shaped_send_is_skipped_without_a_marketing_opt_in(
    session, org_id, worker_engine
) -> None:
    """The recipe sets `purpose: "marketing"`, so silence is not consent whatever category the
    template is registered under at Meta: the step is a recorded skip and nothing is queued."""
    await _template(session, org_id, category="utility")
    await _go_live(session, org_id, _recipe_graph(purpose="marketing"))

    await consumers.fold(_converted(org_id), _resolver)

    (run,) = await _runs(session, org_id)
    assert run.state == "skipped"
    step = await _send_step(session, run.id)
    assert step.state == "skipped" and step.detail == "consent:no_opt_in"
    (message,) = await _messages(session, org_id)
    assert message.state == "suppressed"
    async with session.begin():
        queued = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED)
                )
            )
            .scalars()
            .all()
        )
    assert queued == []


async def test_the_recipe_shaped_send_goes_out_after_an_opt_in(
    session, org_id, worker_engine
) -> None:
    await _template(session, org_id, category="utility")
    await _go_live(session, org_id, _recipe_graph(purpose="marketing"))
    async with session.begin():
        await consent.append(
            session,
            organization_id=org_id,
            subject=consent.subject_for("whatsapp", PHONE),
            purposes=(consent.MARKETING,),
            status=consent.OPTED_IN,
            source="admin",
            evidence="test fixture",
        )

    await consumers.fold(_converted(org_id), _resolver)

    (run,) = await _runs(session, org_id)
    assert run.state == "completed"
    (message,) = await _messages(session, org_id)
    assert message.state == "pending" and message.to_address == PHONE


@pytest.mark.parametrize(
    ("category", "message_state", "step_state"),
    [("utility", "pending", "done"), ("marketing", "suppressed", "skipped")],
)
async def test_without_a_purpose_consent_follows_the_template_category(
    session, org_id, worker_engine, category: str, message_state: str, step_state: str
) -> None:
    """Characterization of the default the recipe avoids: with no `purpose`, whether a contact with
    no opt-in on file is messaged depends on how the tenant registered the template at Meta, which
    this service cannot know. That is why the recipe sets the purpose itself."""
    await _template(session, org_id, category=category)
    await _go_live(session, org_id, _recipe_graph())

    await consumers.fold(_converted(org_id), _resolver)

    (run,) = await _runs(session, org_id)
    step = await _send_step(session, run.id)
    (message,) = await _messages(session, org_id)
    assert (message.state, step.state) == (message_state, step_state)
    if step_state == "skipped":
        assert step.detail == "consent:no_opt_in"
