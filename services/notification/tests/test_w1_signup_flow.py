"""The Embedded Signup worker (slice W1), end to end against a fake Graph.

`start_signup` writes the run and stages the event; `w1_setup.run_setup` is what the consumer runs
after the relay delivers it. Meta is an `httpx.MockTransport`, so every request the worker makes is
inspectable, including the token it carried and everything it must never have written down.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers, w1_selectors, w1_services, w1_setup
from app.config import settings
from app.models import AuditLog, OutboxEvent, ProviderAccount, ProviderTemplate
from app.w1_models import ConnectionHealth, SetupRun, WhatsAppNumber
from platform_core import pii
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for, envelope_for
from tests.w1_helpers import (  # noqa: F401
    CODE,
    TOKEN,
    WABA,
    FakeMeta,
    clean_w1_tables,
    configure_signup,
    connect,
)

pytestmark = pytest.mark.integration

WRITE = "engage.setup.whatsapp.write"
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


@pytest_asyncio.fixture
async def worker_engine(session: AsyncSession) -> AsyncIterator[None]:
    """The engine `session_scope` uses, as the consumer process has it."""
    init_engine(settings())
    yield
    await dispose_engine()


@pytest.fixture
def meta() -> FakeMeta:
    return FakeMeta()


async def start(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    code: str = CODE,
    waba_id: str | None = None,
    phone_number_id: str | None = None,
) -> tuple[uuid.UUID, dict[str, Any]]:
    """`signup/complete`'s service call, and the envelope the relay would deliver for it."""
    async with session.begin():
        run = await w1_services.start_signup(
            session,
            ctx_for(org_id, WRITE, Scope.ORG),
            code=code,
            waba_id=waba_id,
            phone_number_id=phone_number_id,
        )
        setup_id = run.id
    async with session.begin():
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == w1_services.EVT_SETUP_REQUESTED
                    )
                )
            )
            .scalars()
            .all()
        )
    (event,) = [e for e in events if e.payload["setup_id"] == str(setup_id)]
    validate_event_schema(
        w1_services.EVT_SETUP_REQUESTED, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
    )
    return setup_id, envelope_for(org_id, w1_services.EVT_SETUP_REQUESTED, dict(event.payload))


async def run_of(session: AsyncSession, setup_id: uuid.UUID) -> SetupRun:
    async with session.begin():
        stmt = (
            select(SetupRun)
            .where(SetupRun.id == setup_id)
            .execution_options(populate_existing=True)
        )
        return (await session.execute(stmt)).scalar_one()


def states(run: SetupRun) -> dict[str, str]:
    return {s["key"]: s["state"] for s in run.steps}


# --------------------------------------------------------------------- happy path


async def test_the_happy_path_connects_the_number_and_runs_every_step(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    expiry = datetime.now(UTC) + timedelta(days=60)
    meta.token_expires_at = int(expiry.timestamp())
    setup_id, envelope = await start(session, org_id, waba_id=WABA, phone_number_id="1002")
    # Before the worker runs: persisted, running, code sealed, nothing exchanged yet.
    before = await run_of(session, setup_id)
    assert before.status == "running" and set(states(before).values()) == {"waiting"}
    assert before.code_enc is not None and CODE.encode() not in before.code_enc
    assert meta.calls == []

    outcome = await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta))
    assert outcome == "done"

    run = await run_of(session, setup_id)
    assert run.status == "done" and run.error_code is None and run.finished_at is not None
    assert states(run) == {
        "token": "done",
        "number": "done",
        "webhook": "done",
        "templates": "done",
        "verify": "done",
    }
    # Both secrets are spent and wiped.
    assert run.code_enc is None and run.token_enc is None

    async with session.begin():
        account = (await session.execute(select(ProviderAccount))).scalar_one()
        numbers = (await session.execute(select(WhatsAppNumber))).scalars().all()
        health = (await session.execute(select(ConnectionHealth))).scalar_one()
        templates = (await session.execute(select(ProviderTemplate))).scalars().all()
        audits = (await session.execute(select(AuditLog))).scalars().all()
        outbox = (await session.execute(select(OutboxEvent))).scalars().all()
    assert (account.waba_id, account.phone_number_id, account.status) == (
        WABA,
        "1002",
        "connected",
    )
    assert account.access_token_enc is not None
    assert pii.open_sealed(account.access_token_enc, settings=settings()) == TOKEN
    assert {n.phone_number_id: n.is_default for n in numbers} == {"1001": False, "1002": True}
    assert {n.phone_number_id: n.quality_rating for n in numbers} == {
        "1001": "green",
        "1002": "yellow",
    }
    assert health.token_state == "valid" and health.webhook_subscribed_at is not None
    # The expiry Meta reported survives the later steps' health updates.
    assert health.token_expires_at is not None
    assert abs((health.token_expires_at - expiry).total_seconds()) < 2
    assert [t.provider_name for t in templates] == ["order_update"]

    # The token goes only in the Authorization header of the calls that need it; the code and the
    # app secret only on the exchange.
    exchange = [c for c in meta.calls if c.url.path.endswith("/oauth/access_token")]
    assert len(exchange) == 1
    assert exchange[0].url.params["code"] == CODE
    for call in meta.calls:
        if call not in exchange:
            assert CODE not in str(call.url)
    authed = [c for c in meta.calls if c not in exchange]
    assert all(
        TOKEN in c.headers["authorization"] or "|" in c.headers["authorization"] for c in authed
    )
    assert any(
        c.method == "POST" and c.url.path.endswith(f"/{WABA}/subscribed_apps") for c in meta.calls
    )

    # Nothing written down anywhere carries a secret.
    evidence = json.dumps(
        [
            [a.after for a in audits],
            [o.payload for o in outbox],
            run.steps,
            run.error_code,
        ],
        default=str,
    )
    logged = capsys.readouterr().out + caplog.text
    for secret in (CODE, TOKEN):
        assert secret not in evidence
        assert secret not in logged

    # A redelivery of the same event does nothing.
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "redelivered"
    assert len(exchange) == 1


async def test_the_consumer_entry_point_runs_the_same_setup(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_id, envelope = await start(session, org_id)
    real = w1_setup.run_setup

    async def with_mock(env: dict[str, Any], **kwargs: Any) -> str:
        return await real(env, transport=httpx.MockTransport(meta), **kwargs)

    monkeypatch.setattr(w1_setup, "run_setup", with_mock)
    assert await consumers.run_whatsapp_setup(envelope) == "done"
    assert (await run_of(session, setup_id)).status == "done"


# --------------------------------------------------------------------- each step failing

FAILURES = [
    # (route to break, status, expected failing step, expected error_code)
    ("oauth", 400, "token", "code_exchange_failed"),
    ("oauth", 503, "token", "meta_unreachable"),
    ("numbers", 500, "number", "meta_unreachable"),
    ("number", 403, "number", "ownership_check_failed"),
    ("subscribe", 400, "webhook", "webhook_subscribe_failed"),
    ("templates", 500, "templates", "templates_sync_failed"),
]
ORDER = ["token", "number", "webhook", "templates", "verify"]


@pytest.mark.parametrize(("route", "status", "step", "code"), FAILURES)
async def test_each_step_fails_with_a_machine_readable_code_and_stops_there(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
    route: str,
    status: int,
    step: str,
    code: str,
) -> None:
    meta.fail[route] = status
    setup_id, envelope = await start(session, org_id)
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "failed"

    run = await run_of(session, setup_id)
    assert run.status == "failed" and run.error_code == code
    got = states(run)
    index = ORDER.index(step)
    assert got[step] == "failed"
    assert [got[k] for k in ORDER[:index]] == ["done"] * index
    assert [got[k] for k in ORDER[index + 1 :]] == ["waiting"] * (len(ORDER) - index - 1)
    failed = next(s for s in run.steps if s["key"] == step)
    assert failed["detail"] and TOKEN not in failed["detail"] and CODE not in failed["detail"]
    # Spent secrets are gone even on failure.
    assert run.code_enc is None and run.token_enc is None


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda m: m.numbers.clear(), "no_phone_number"),
        (lambda m: m.granted.clear() or m.granted.append("7777"), "waba_not_accessible"),
        (lambda m: m.subscribed.clear(), "webhook_not_subscribed"),
    ],
    ids=["no_number", "waba_not_shared", "not_subscribed"],
)
async def test_discovery_and_verification_failures_have_their_own_codes(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
    mutate: Any,
    code: str,
) -> None:
    mutate(meta)
    setup_id, envelope = await start(session, org_id, waba_id=WABA)
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "failed"
    assert (await run_of(session, setup_id)).error_code == code


async def test_a_number_another_organization_holds_is_refused_and_nothing_is_stored(
    session: AsyncSession,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=other,
                channel="whatsapp",
                waba_id="8888",
                phone_number_id="1002",
                display_number="+910000000000",
                status="connected",
            )
        )
    setup_id, envelope = await start(session, org_id, phone_number_id="1002")
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "failed"

    run = await run_of(session, setup_id)
    assert run.error_code == "number_claimed" and states(run)["number"] == "failed"
    async with session.begin():
        assert (await session.execute(select(ProviderAccount))).scalars().all() == []
        assert (await session.execute(select(WhatsAppNumber))).scalars().all() == []


async def test_a_signup_over_an_existing_connection_replaces_it_in_place(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    await connect(session, org_id, phone_number_id="5555", token="ci-only-old-token")
    _, envelope = await start(session, org_id, phone_number_id="1001")
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "done"
    async with session.begin():
        (account,) = (await session.execute(select(ProviderAccount))).scalars().all()
    assert account.phone_number_id == "1001"
    assert pii.open_sealed(account.access_token_enc or b"", settings=settings()) == TOKEN
    assert meta.unsubscribes() == []  # the same WABA: nothing was left


OLD_TOKEN = "ci-only-old-token"  # pragma: allowlist secret


async def _replace_on_another_waba(
    session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> SetupRun:
    """An organization connected on WABA 8000 signs up again on WABA 9001."""
    await connect(
        session,
        org_id,
        waba_id="8000",
        phone_number_id="5555",
        token=OLD_TOKEN,
        cached=[("5555", "+1 555 010 0000"), ("5556", "+1 555 010 0001")],
    )
    setup_id, envelope = await start(session, org_id, phone_number_id="1001")
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "done"
    return await run_of(session, setup_id)


async def test_a_signup_on_another_waba_retires_the_old_wabas_numbers_and_subscription(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    await _replace_on_another_waba(session, org_id, meta)
    async with session.begin():
        numbers = {
            n.phone_number_id: n for n in (await session.execute(select(WhatsAppNumber))).scalars()
        }
    # Nothing cached from the WABA the organization left is live or a default any more.
    assert (numbers["5555"].status, numbers["5555"].is_default) == ("disconnected", False)
    assert (numbers["5556"].status, numbers["5556"].is_default) == ("disconnected", False)
    assert (numbers["1001"].status, numbers["1001"].is_default) == ("connected", True)
    # The app was unsubscribed from the old WABA with the old token, after the new one was stored.
    (call,) = meta.unsubscribes()
    assert call.url.path.endswith("/8000/subscribed_apps")
    assert call.headers["authorization"] == f"Bearer {OLD_TOKEN}"
    assert meta.calls.index(call) > 0


async def test_a_failed_unsubscribe_from_the_old_waba_does_not_fail_the_signup(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    meta.fail = {"unsubscribe": 500}
    run = await _replace_on_another_waba(session, org_id, meta)
    assert run.status == "done" and run.error_code is None
    (number_step,) = [s for s in run.steps if s["key"] == "number"]
    assert "could not be removed" in number_step["detail"]
    assert OLD_TOKEN not in json.dumps(run.steps) and TOKEN not in json.dumps(run.steps)
    async with session.begin():
        (account,) = (await session.execute(select(ProviderAccount))).scalars().all()
    assert (account.waba_id, account.phone_number_id) == (WABA, "1001")


# --------------------------------------------------------------------- tenancy and idempotency


async def test_an_event_naming_another_organizations_run_touches_nothing(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    setup_id, envelope = await start(session, org_id)
    intruder = uuid.uuid4()
    forged = envelope_for(intruder, w1_services.EVT_SETUP_REQUESTED, envelope["payload"])
    forged["payload"] = {"organization_id": str(intruder), "setup_id": str(setup_id)}
    assert await w1_setup.run_setup(forged, transport=httpx.MockTransport(meta)) == "missing"
    assert meta.calls == []
    assert (await run_of(session, setup_id)).status == "running"


async def test_a_retry_of_the_same_code_returns_the_same_run_and_one_event(
    session: AsyncSession, org_id: uuid.UUID, signup_configured: None
) -> None:
    first, _ = await start(session, org_id)
    second, _ = await start(session, org_id)
    assert first == second
    async with session.begin():
        runs = (await session.execute(select(SetupRun))).scalars().all()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == w1_services.EVT_SETUP_REQUESTED
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(runs) == 1 and len(events) == 1


async def test_a_second_setup_while_one_is_running_is_refused(
    session: AsyncSession, org_id: uuid.UUID, signup_configured: None
) -> None:
    await start(session, org_id, code="first-code")
    with pytest.raises(Exception) as caught:
        await start(session, org_id, code="second-code")
    assert getattr(caught.value, "errors", None) == [{"code": "setup_in_progress"}]


async def test_a_stale_running_setup_is_abandoned_and_a_new_one_starts_under_the_unique_index(
    session: AsyncSession, org_id: uuid.UUID, signup_configured: None
) -> None:
    """The stale run leaves `running` in the same flush that inserts its replacement, so the
    one-running-setup index never sees two."""
    async with session.begin():
        session.add(
            SetupRun(
                organization_id=org_id,
                status="running",
                steps=[],
                code_hash="stale-code-hash",
                created_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
    setup_id, _ = await start(session, org_id, code="fresh-code")
    async with session.begin():
        runs = {r.code_hash: r for r in (await session.execute(select(SetupRun))).scalars()}
    stale = runs.pop("stale-code-hash")
    (fresh,) = runs.values()
    assert (stale.status, stale.error_code) == ("failed", "abandoned")
    assert (fresh.id, fresh.status) == (setup_id, "running")


async def test_a_second_setup_that_passes_the_read_is_refused_by_the_database(
    session: AsyncSession,
    org_id: uuid.UUID,
    signup_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read is not the guard: with it blinded, the unique index on running setups still stops
    the second insert, and it is reported as the same `setup_in_progress`."""
    await start(session, org_id, code="first-code")

    async def nothing_running(*_args: Any, **_kwargs: Any) -> list[SetupRun]:
        return []

    monkeypatch.setattr(w1_selectors, "running_runs", nothing_running)
    with pytest.raises(Exception) as caught:
        await start(session, org_id, code="second-code")
    assert getattr(caught.value, "errors", None) == [{"code": "setup_in_progress"}]
    async with session.begin():
        runs = (await session.execute(select(SetupRun))).scalars().all()
    assert len(runs) == 1


async def test_a_number_quality_webhook_updates_only_this_organizations_cache(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    other = uuid.uuid4()
    await connect(session, org_id, cached=[("1001", "+91 98765 43210")])
    async with admin_session.begin():
        admin_session.add(
            WhatsAppNumber(
                organization_id=other,
                waba_id=WABA,
                phone_number_id="2001",
                display_phone_number="+91 98765 43210",
                messaging_limit="TIER_1K",
                refreshed_at=None,
            )
        )
    async with session.begin():
        await w1_services.update_number_quality(
            session,
            organization_id=org_id,
            waba_id=WABA,
            display_phone_number="+919876543210",
            event="UPGRADE",
            current_limit="TIER_10K",
        )
    async with session.begin():
        (mine,) = (await session.execute(select(WhatsAppNumber))).scalars().all()
    assert mine.messaging_limit == "TIER_10K" and mine.refreshed_at is None
    async with admin_session.begin():
        theirs = (
            await admin_session.execute(
                select(WhatsAppNumber).where(WhatsAppNumber.organization_id == other)
            )
        ).scalar_one()
    assert theirs.messaging_limit == "TIER_1K"


async def test_the_quality_handler_is_registered_with_the_webhook_receiver() -> None:
    from app import w2_webhooks
    from app.main import app  # noqa: F401  (importing the app registers the router and handler)

    assert w2_webhooks._number_quality_handler is w1_services.update_number_quality


async def test_a_connected_setup_is_sendable_and_a_disconnect_stops_sending(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    """The row signup writes is exactly what the tenant transport sends from, and disconnecting
    it (which wipes the token) leaves the dispatcher with no transport to send on."""
    from app import tenant_transport
    from platform_core.db import session_scope

    def scope(organization_id: uuid.UUID) -> Any:
        return session_scope(organization_id=organization_id)

    _, envelope = await start(session, org_id)
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "done"

    ready = await tenant_transport.resolve(org_id, "whatsapp", scope=scope, cfg=settings())
    assert not isinstance(ready, tenant_transport.ChannelUnavailable)

    async with session.begin():
        await w1_services.disconnect(session, ctx_for(org_id, WRITE, Scope.ORG))
    gone = await tenant_transport.resolve(org_id, "whatsapp", scope=scope, cfg=settings())
    assert isinstance(gone, tenant_transport.ChannelUnavailable)
    assert gone.reason == "channel_not_connected"


async def test_a_step_that_fails_after_touching_the_run_is_still_recorded(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step's savepoint is rolled back, which expires what it touched; the failure must still
    land on the run (and an unexpected exception must not leak its text)."""

    async def breaks(session: AsyncSession, run: SetupRun, ctx: Any) -> str:
        run.error_code = "half_written"
        await session.flush()
        raise RuntimeError(f"boom {TOKEN}")

    monkeypatch.setitem(w1_setup._STEP_FUNCS, "webhook", breaks)
    setup_id, envelope = await start(session, org_id)
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "failed"

    run = await run_of(session, setup_id)
    assert (run.status, run.error_code) == ("failed", "internal_error")
    assert states(run)["number"] == "done" and states(run)["webhook"] == "failed"
    failed = next(s for s in run.steps if s["key"] == "webhook")
    assert failed["detail"] == "Something went wrong on our side."
    assert TOKEN not in json.dumps(run.steps)
