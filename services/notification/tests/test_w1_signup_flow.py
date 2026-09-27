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

from app import consumers, selectors, w1_selectors, w1_services, w1_setup
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
    meta: FakeMeta | None = None,
    code: str = CODE,
    waba_id: str | None = None,
    phone_number_id: str | None = None,
) -> tuple[uuid.UUID, dict[str, Any]]:
    """`signup/complete`'s service call (which exchanges the code against `meta`), and the
    envelope the relay would deliver for it."""
    setup_id = await w1_services.start_signup(
        session,
        ctx_for(org_id, WRITE, Scope.ORG),
        code=code,
        waba_id=waba_id,
        phone_number_id=phone_number_id,
        transport=httpx.MockTransport(meta or FakeMeta()),
    )
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
    setup_id, envelope = await start(
        session, org_id, meta=meta, waba_id=WABA, phone_number_id="1002"
    )
    # Before the worker runs: the request already exchanged the code (and only that), so the
    # token is sealed on the run, the code was never stored, and every other step is waiting.
    before = await run_of(session, setup_id)
    assert before.status == "running"
    assert states(before) == {
        "token": "done",
        "number": "waiting",
        "webhook": "waiting",
        "register": "waiting",
        "templates": "waiting",
        "verify": "waiting",
    }
    assert before.code_enc is None
    assert before.token_enc is not None and TOKEN.encode() not in before.token_enc
    assert meta.routes_called() == ["oauth/access_token"]

    outcome = await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta))
    assert outcome == "done"

    run = await run_of(session, setup_id)
    assert run.status == "done" and run.error_code is None and run.finished_at is not None
    assert states(run) == {
        "token": "done",
        "number": "done",
        "webhook": "done",
        "register": "done",
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
    setup_id, envelope = await start(session, org_id, meta=meta)
    real = w1_setup.run_setup

    async def with_mock(env: dict[str, Any], **kwargs: Any) -> str:
        return await real(env, transport=httpx.MockTransport(meta), **kwargs)

    monkeypatch.setattr(w1_setup, "run_setup", with_mock)
    assert await consumers.run_whatsapp_setup(envelope) == "done"
    assert (await run_of(session, setup_id)).status == "done"


# --------------------------------------------------------------------- each step failing

FAILURES = [
    # (route to break, status, expected failing step, expected error_code). The code exchange is
    # not here: it happens in the request, see the tests on `start_signup` below.
    ("numbers", 500, "number", "meta_unreachable"),
    ("number", 403, "number", "ownership_check_failed"),
    ("subscribe", 400, "webhook", "webhook_subscribe_failed"),
    ("templates", 500, "templates", "templates_sync_failed"),
]
ORDER = ["token", "number", "webhook", "register", "templates", "verify"]


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
    setup_id, envelope = await start(session, org_id, meta=meta)
    meta.fail[route] = status
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
    setup_id, envelope = await start(session, org_id, meta=meta, waba_id=WABA)
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
    setup_id, envelope = await start(session, org_id, meta=meta, phone_number_id="1002")
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
    _, envelope = await start(session, org_id, meta=meta, phone_number_id="1001")
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
    setup_id, envelope = await start(session, org_id, meta=meta, phone_number_id="1001")
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
    setup_id, envelope = await start(session, org_id, meta=meta)
    intruder = uuid.uuid4()
    forged = envelope_for(intruder, w1_services.EVT_SETUP_REQUESTED, envelope["payload"])
    forged["payload"] = {"organization_id": str(intruder), "setup_id": str(setup_id)}
    assert await w1_setup.run_setup(forged, transport=httpx.MockTransport(meta)) == "missing"
    assert meta.routes_called() == ["oauth/access_token"]  # only the request's own exchange
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

    _, envelope = await start(session, org_id, meta=meta)
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
    setup_id, envelope = await start(session, org_id, meta=meta)
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "failed"

    run = await run_of(session, setup_id)
    assert (run.status, run.error_code) == ("failed", "internal_error")
    assert states(run)["number"] == "done" and states(run)["webhook"] == "failed"
    failed = next(s for s in run.steps if s["key"] == "webhook")
    assert failed["detail"] == "Something went wrong on our side."
    assert TOKEN not in json.dumps(run.steps)


# --------------------------------------------------------------------- the exchange, in the request


@pytest.fixture(autouse=True)
def _no_verify_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(w1_setup, "VERIFY_WAIT_SECONDS", 0)


def _oauth_error(message: str, *, subcode: int | None = None, code: int = 100) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "type": "OAuthException"}
    if subcode is not None:
        error["error_subcode"] = subcode
    return {"status": 400, "error": error}


async def _setup_error(
    session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta, code: str = CODE
) -> Any:
    with pytest.raises(Exception) as caught:
        await start(session, org_id, meta=meta, code=code)
    return caught.value


@pytest.mark.parametrize(
    "error",
    [
        _oauth_error("This authorization code has expired.", subcode=36009),
        _oauth_error("This authorization code has been used.", subcode=36007),
        _oauth_error("Error validating verification code. Please make sure your redirect_uri."),
    ],
    ids=["expired_subcode", "used_subcode", "message_only"],
)
async def test_a_spent_or_expired_code_fails_the_request_with_a_specific_code(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    error: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    meta.oauth_error = error
    raised = await _setup_error(session, org_id, meta)
    assert getattr(raised, "errors", None) == [{"code": "code_expired"}]
    assert "expired or was already used" in str(raised.detail)

    async with session.begin():
        (run,) = (await session.execute(select(SetupRun))).scalars().all()
        events = (await session.execute(select(OutboxEvent))).scalars().all()
    # Recorded as a failed run whose token step says why, holding no secret, and queueing nothing.
    assert (run.status, run.error_code) == ("failed", "code_expired")
    assert states(run)["token"] == "failed" and states(run)["number"] == "waiting"
    assert run.code_enc is None and run.token_enc is None
    assert [e for e in events if e.event_type == w1_services.EVT_SETUP_REQUESTED] == []
    assert CODE not in json.dumps(run.steps) + caplog.text


async def test_any_other_refusal_of_the_code_is_a_generic_exchange_failure(
    session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta, signup_configured: None
) -> None:
    meta.oauth_error = _oauth_error("Invalid client secret", code=1)
    raised = await _setup_error(session, org_id, meta)
    assert getattr(raised, "errors", None) == [{"code": "code_exchange_failed"}]
    async with session.begin():
        (run,) = (await session.execute(select(SetupRun))).scalars().all()
    assert (run.status, run.error_code) == ("failed", "code_exchange_failed")


async def test_a_meta_outage_during_the_exchange_leaves_the_same_code_usable(
    session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta, signup_configured: None
) -> None:
    meta.oauth_error = {"status": 503, "error": {"code": 2, "message": "down"}}
    raised = await _setup_error(session, org_id, meta)
    assert getattr(raised, "errors", None) == [{"code": "meta_unreachable"}]
    assert raised.status_code == 503
    async with session.begin():
        assert (await session.execute(select(SetupRun))).scalars().all() == []

    meta.oauth_error = None
    setup_id, _ = await start(session, org_id, meta=meta)
    assert (await run_of(session, setup_id)).status == "running"


async def test_a_repeated_code_never_exchanges_a_spent_code_again(
    session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta, signup_configured: None
) -> None:
    first, _ = await start(session, org_id, meta=meta)
    second, _ = await start(session, org_id, meta=meta)
    assert first == second
    assert meta.routes_called() == ["oauth/access_token"]  # one exchange, ever
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
    assert len(events) == 1


async def test_a_worker_redelivery_does_not_exchange_again(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    setup_id, envelope = await start(session, org_id, meta=meta)
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "done"
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "redelivered"
    assert meta.routes_called().count("oauth/access_token") == 1
    assert (await run_of(session, setup_id)).status == "done"


# --------------------------------------------------------------------- registering the number


def _pending(meta: FakeMeta, pnid: str = "1001") -> None:
    for item in meta.numbers:
        if item["id"] == pnid:
            item["status"] = "PENDING"
            item["platform_type"] = "NOT_APPLICABLE"
            item["code_verification_status"] = "NOT_VERIFIED"


async def _number_row(session: AsyncSession, pnid: str = "1001") -> WhatsAppNumber:
    async with session.begin():
        stmt = (
            select(WhatsAppNumber)
            .where(WhatsAppNumber.phone_number_id == pnid)
            .execution_options(populate_existing=True)
        )
        return (await session.execute(stmt)).scalar_one()


async def _run(
    session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta, *, code: str = CODE
) -> SetupRun:
    setup_id, envelope = await start(
        session, org_id, meta=meta, code=code, waba_id=WABA, phone_number_id="1001"
    )
    await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta))
    return await run_of(session, setup_id)


async def test_a_connected_number_is_not_registered_again(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    run = await _run(session, org_id, meta)
    assert run.status == "done"
    assert meta.registers() == []
    (step,) = [s for s in run.steps if s["key"] == "register"]
    assert step["state"] == "done" and step["detail"] == "Number already registered"
    row = await _number_row(session)
    assert row.registration_pin_sealed is None and row.registration_attempts == 0


async def test_an_unregistered_number_is_registered_once_with_a_sealed_random_pin(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _pending(meta)
    run = await _run(session, org_id, meta)
    assert run.status == "done" and run.error_code is None

    (call,) = meta.registers()
    assert call.url.path.endswith("/1001/register") and call.method == "POST"
    assert call.headers["authorization"] == f"Bearer {TOKEN}"
    (body,) = meta.register_bodies
    assert body["messaging_product"] == "whatsapp"
    pin = body["pin"]
    assert isinstance(pin, str) and len(pin) == 6 and pin.isdigit()
    assert pin not in str(call.url)

    row = await _number_row(session)
    assert row.registration_attempts == 1 and row.registration_attempted_at is not None
    assert row.registration_pin_sealed is not None
    assert pin.encode() not in row.registration_pin_sealed
    assert pii.open_sealed(row.registration_pin_sealed, settings=settings()) == pin
    assert (row.status, row.platform_type) == ("connected", "CLOUD_API")

    # The PIN is written down nowhere but the sealed column.
    async with session.begin():
        audits = (await session.execute(select(AuditLog))).scalars().all()
        outbox = (await session.execute(select(OutboxEvent))).scalars().all()
    evidence = json.dumps(
        [[a.after for a in audits], [o.payload for o in outbox], run.steps], default=str
    )
    assert pin not in evidence
    assert pin not in capsys.readouterr().out + caplog.text


async def test_an_already_registered_answer_from_meta_counts_as_registered(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    _pending(meta)
    meta.register_error = {
        "status": 400,
        "error": {"code": 100, "message": "Phone number is already registered."},
    }
    run = await _run(session, org_id, meta)
    (step,) = [s for s in run.steps if s["key"] == "register"]
    assert step["state"] == "done" and step["detail"] == "Number already registered"
    # The verify step is what then decides whether the number can send: it still is pending.
    assert run.error_code == "number_not_connected"


async def test_a_number_with_two_step_verification_fails_with_pin_required(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    _pending(meta)
    meta.register_error = {
        "status": 400,
        "error": {"code": 133005, "message": "Two step verification PIN Mismatch"},
    }
    run = await _run(session, org_id, meta)
    assert (run.status, run.error_code) == ("failed", "pin_required")
    got = states(run)
    assert got["register"] == "failed"
    assert got["templates"] == "waiting" and got["verify"] == "waiting"
    (step,) = [s for s in run.steps if s["key"] == "register"]
    assert "two-step verification" in step["detail"]
    assert len(meta.registers()) == 1  # never retried inside the run
    # The attempt and the PIN survive the failed step: they are what stop a blind retry.
    row = await _number_row(session)
    assert row.registration_attempts == 1 and row.registration_pin_sealed is not None


async def test_a_second_attempt_reuses_the_stored_pin_and_counts_the_attempt(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    _pending(meta)
    meta.register_error = {"status": 500, "error": {"code": 2, "message": "boom"}}
    first = await _run(session, org_id, meta, code="code-one")
    assert first.error_code == "meta_unreachable" and states(first)["register"] == "failed"

    meta.register_error = None
    second = await _run(session, org_id, meta, code="code-two")
    assert second.status == "done"
    assert meta.register_bodies[0]["pin"] == meta.register_bodies[1]["pin"]
    assert (await _number_row(session)).registration_attempts == 2


async def test_registration_stops_short_of_metas_ten_calls_in_72_hours(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    _pending(meta)
    meta.register_error = {"status": 500, "error": {"code": 2, "message": "boom"}}
    await _run(session, org_id, meta, code="code-one")  # caches the number, one attempt
    row = await _number_row(session)
    async with session.begin():
        cached = await session.get(WhatsAppNumber, row.id)
        assert cached is not None
        cached.registration_attempts = w1_setup.REGISTER_ATTEMPT_CAP
        cached.registration_attempted_at = datetime.now(UTC) - timedelta(hours=1)
    meta.register_bodies.clear()
    meta.calls.clear()
    meta.register_error = None

    limited = await _run(session, org_id, meta, code="code-two")
    assert (limited.status, limited.error_code) == ("failed", "register_rate_limited")
    assert meta.registers() == []  # Meta was never asked

    # Past the window the count starts over.
    async with session.begin():
        cached = await session.get(WhatsAppNumber, row.id)
        assert cached is not None
        cached.registration_attempted_at = datetime.now(UTC) - timedelta(hours=73)
    again = await _run(session, org_id, meta, code="code-three")
    assert again.status == "done" and len(meta.registers()) == 1
    assert (await _number_row(session)).registration_attempts == 1


@pytest.mark.parametrize(
    ("meta_code", "expected"),
    [
        (133006, "number_not_verified"),
        (133016, "register_rate_limited"),
        (131000, "register_failed"),
    ],
)
async def test_other_register_refusals_have_their_own_codes(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
    meta_code: int,
    expected: str,
) -> None:
    _pending(meta)
    meta.register_error = {"status": 400, "error": {"code": meta_code, "message": "x"}}
    assert (await _run(session, org_id, meta)).error_code == expected


async def test_a_number_that_never_becomes_connected_fails_the_verify_step_with_a_reason(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    """Registration succeeded but Meta still calls the number pending: the wizard must not say
    "done", because a send would fail."""
    _pending(meta)
    meta.register_connects = False
    run = await _run(session, org_id, meta)
    assert (run.status, run.error_code) == ("failed", "number_not_connected")
    got = states(run)
    assert got["register"] == "done" and got["templates"] == "done" and got["verify"] == "failed"
    (step,) = [s for s in run.steps if s["key"] == "verify"]
    assert "pending" in step["detail"] and "cannot send" in step["detail"]
    # Asked a few times before giving up, since Meta is slow to flip the status.
    reads = [c for c in meta.calls if c.method == "GET" and c.url.path.endswith("/1001")]
    assert len(reads) >= w1_setup.VERIFY_READS


async def test_a_run_queued_before_the_register_step_existed_gains_it(
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    setup_id, envelope = await start(session, org_id, meta=meta)
    async with session.begin():
        run = await w1_selectors.get_run(session, org_id, setup_id, for_update=True)
        assert run is not None
        run.steps = [s for s in run.steps if s["key"] != "register"]
    assert await w1_setup.run_setup(envelope, transport=httpx.MockTransport(meta)) == "done"
    assert "register" in states(await run_of(session, setup_id))


# ------------------------------------------------------------------ disconnect releases the number


async def test_disconnect_releases_the_number_so_another_organization_can_claim_it(
    session: AsyncSession,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    worker_engine: None,
) -> None:
    first = await _run(session, org_id, meta)
    assert first.status == "done"

    # While connected, a second organization is refused the number. (The rival's own signup runs
    # as that organization: its transaction is bound to it by the worker's scope, the request
    # side by `admin_session`, which bypasses row-level security.)
    rival = uuid.uuid4()
    rival_id, rival_env = await start(
        admin_session, rival, meta=meta, code="rival-one", phone_number_id="1001"
    )
    assert await w1_setup.run_setup(rival_env, transport=httpx.MockTransport(meta)) == "failed"
    async with admin_session.begin():
        blocked = (
            await admin_session.execute(select(SetupRun).where(SetupRun.id == rival_id))
        ).scalar_one()
    assert blocked.error_code == "number_claimed"

    async with session.begin():
        released = await w1_services.disconnect(session, ctx_for(org_id, WRITE, Scope.ORG))
    assert released.waba_id == WABA

    async with admin_session.begin():
        account = (
            await admin_session.execute(
                select(ProviderAccount).where(ProviderAccount.organization_id == org_id)
            )
        ).scalar_one()
        audit = (
            await admin_session.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_id,
                    AuditLog.action == "engage.whatsapp.disconnected",
                )
            )
        ).scalar_one()
        # A webhook naming the old number no longer resolves to anybody.
        assert await selectors.account_for_phone_number(admin_session, "1001") is None
    assert account.status == "disconnected" and account.phone_number_id is None
    assert account.released_phone_number_id == "1001"
    assert audit.after["released_phone_number_id"] == "1001"  # history kept

    # Now the other organization can have it, and the first one's history is untouched.
    _, env = await start(admin_session, rival, meta=meta, code="rival-two", phone_number_id="1001")
    assert await w1_setup.run_setup(env, transport=httpx.MockTransport(meta)) == "done"
    async with admin_session.begin():
        holder = await selectors.account_for_phone_number(admin_session, "1001")
    assert holder is not None and holder.organization_id == rival
