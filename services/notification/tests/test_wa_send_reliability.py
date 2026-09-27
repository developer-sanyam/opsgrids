"""WhatsApp send reliability: retry, classification by Meta's error code, connection health, the
pre-send template re-check.

Two layers. The classification tests are pure (`transports.classify_meta_error`, plus the real
`WhatsAppCloudTransport` against a stubbed HTTP client). Everything else drives the real
`dispatcher.dispatch` and the real retry sweep against PostgreSQL as the **app** role (row-level
security live), with discovery on the real outbox role — so the column grant in
`migrations/env.py` is exercised, not assumed. Nothing opens a socket.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import dispatcher, models, selectors, services, transports, w1_selectors, w1_services
from app.config import Settings, settings
from app.cron import CampaignDriver
from app.domain import STATE_FAILED, STATE_PENDING, STATE_SENT
from app.models import SCHEMA, Delivery, Message, OutboxEvent, ProviderAccount
from platform_core import pii
from platform_core.db import TENANT_GUC, dispose_engine, init_engine, session_scope

pytestmark = pytest.mark.integration

TOKEN = "tenant-whatsapp-token-3f9c"
PHONE = "111111111111"
TO = "+919999900001"


# --------------------------------------------------------------------- classification (pure)


def _err(code: int, message: str = "boom", *, subcode: int | None = None, status: int = 400) -> Any:
    body: dict[str, Any] = {"error": {"message": message, "type": "OAuthException", "code": code}}
    if subcode is not None:
        body["error"]["error_subcode"] = subcode
    return transports.classify_meta_error(status, body, "")


@pytest.mark.parametrize("code", [130429, 80007, 131056, 131000, 131016, 133004, 2, 1])
def test_throttles_and_outages_are_retried(code: int) -> None:
    exc = _err(code)
    assert type(exc) is transports.TransportUnavailable
    assert exc.code == str(code)


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (131049, "per_user_marketing_limit"),
        (131048, "spam_rate_limit"),
        (131026, "undeliverable"),
        (131047, "outside_service_window"),
        (131051, "unsupported_message_type"),
        (131050, "user_stopped_marketing"),
        (131042, "payment_issue"),
        (132000, "template_parameter_count"),
        (132001, "template_not_found_or_not_approved"),
        (132005, "template_text_too_long"),
        (132007, "template_policy_violation"),
        (132012, "template_parameter_format"),
        (132015, "template_paused"),
        (132016, "template_disabled"),
    ],
)
def test_per_message_refusals_are_terminal_with_a_reason(code: int, reason: str) -> None:
    # Even on an HTTP status that looks transient: the code decides, not the status.
    exc = _err(code, status=500)
    assert type(exc) is transports.TransportRefused
    assert exc.code == str(code)
    assert exc.reason == reason


@pytest.mark.parametrize("code", [190, 463, 467, 0])
def test_dead_token_errors_are_credential_rejections(code: int) -> None:
    exc = _err(code)
    assert isinstance(exc, transports.CredentialsRejected)
    assert isinstance(exc, transports.TransportRefused)  # terminal for the message too


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (368, "account_temporarily_blocked"),
        (3, "permission_denied"),
        (10, "permission_denied"),
        (200, "permission_denied"),
        (250, "permission_denied"),
        (299, "permission_denied"),
    ],
)
def test_policy_blocks_and_permission_errors_are_terminal_for_the_message_only(
    code: int, reason: str
) -> None:
    exc = _err(code, "no way")
    assert type(exc) is transports.TransportRefused  # not CredentialsRejected
    assert (exc.code, exc.reason) == (str(code), reason)
    assert str(exc) == f"{code}: no way"


def test_a_token_subcode_marks_the_credential_even_under_another_code() -> None:
    assert isinstance(_err(100, subcode=463), transports.CredentialsRejected)


def test_the_stored_text_is_the_code_and_metas_message_not_the_http_status() -> None:
    exc = transports.classify_meta_error(
        400,
        {
            "error": {
                "code": 131049,
                "message": "(#131049) This message was not delivered",
                "error_data": {"details": "healthy ecosystem engagement"},
            }
        },
        '{"raw": "ignored"}',
    )
    assert str(exc) == (
        "131049: (#131049) This message was not delivered - healthy ecosystem engagement"
    )
    assert "400" not in str(exc)


def test_an_unlisted_code_falls_back_to_the_status() -> None:
    assert type(_err(999999, status=400)) is transports.TransportRefused
    assert type(_err(999999, status=503)) is transports.TransportUnavailable


def test_a_body_with_no_code_falls_back_to_the_status() -> None:
    assert type(transports.classify_meta_error(502, None, "<html>bad gateway</html>")) is (
        transports.TransportUnavailable
    )
    assert type(transports.classify_meta_error(429, {}, "")) is transports.TransportUnavailable
    assert type(transports.classify_meta_error(400, None, "nope")) is transports.TransportRefused
    assert isinstance(
        transports.classify_meta_error(401, None, "nope"), transports.CredentialsRejected
    )


def test_with_message_keeps_the_class_code_and_reason() -> None:
    exc = _err(190, "token abc expired")
    scrubbed = exc.with_message("token <redacted> expired")
    assert type(scrubbed) is type(exc)
    assert (scrubbed.code, scrubbed.reason) == (exc.code, exc.reason)
    assert str(scrubbed) == "token <redacted> expired"


# --------------------------------------------------------------------- the real client


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub `httpx.AsyncClient`. Set `state["respond"]` to a `(request) -> Response` callable."""
    state: dict[str, Any] = {"requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["respond"](request)

    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return state


def _client() -> transports.WhatsAppCloudTransport:
    return transports.WhatsAppCloudTransport(
        api_base="https://graph.example.com/v21.0",
        phone_number_id=PHONE,
        access_token=TOKEN,
        timeout=1.0,
    )


async def _send(http: dict[str, Any]) -> transports.Receipt:
    return await _client().send(transports.Outgoing(to_address=TO, subject=None, body="Hello"))


async def test_a_meta_error_body_is_classified_on_its_code_through_the_real_client(http) -> None:
    http["respond"] = lambda r: httpx.Response(
        429, json={"error": {"code": 131049, "message": "marketing limit"}}
    )
    with pytest.raises(transports.TransportRefused) as caught:
        await _send(http)
    assert caught.value.code == "131049"

    http["respond"] = lambda r: httpx.Response(
        400, json={"error": {"code": 130429, "message": "throughput"}}
    )
    with pytest.raises(transports.TransportUnavailable) as caught_transient:
        await _send(http)
    assert caught_transient.value.code == "130429"


async def test_a_non_json_success_body_does_not_crash_the_send(http) -> None:
    http["respond"] = lambda r: httpx.Response(200, text="<html>ok?</html>")
    receipt = await _send(http)
    assert receipt.provider_message_id is None
    assert receipt.message_status is None


async def test_a_non_json_error_body_is_classified_by_status_not_a_crash(http) -> None:
    http["respond"] = lambda r: httpx.Response(502, text="<html>bad gateway</html>")
    with pytest.raises(transports.TransportUnavailable):
        await _send(http)
    http["respond"] = lambda r: httpx.Response(400, text="<html>nope</html>")
    with pytest.raises(transports.TransportRefused):
        await _send(http)


async def test_an_oddly_shaped_success_body_yields_a_receipt_without_an_id(http) -> None:
    http["respond"] = lambda r: httpx.Response(200, json={"messages": "not a list"})
    assert (await _send(http)).provider_message_id is None
    http["respond"] = lambda r: httpx.Response(200, json=[1, 2])
    assert (await _send(http)).provider_message_id is None


async def test_message_status_is_captured_from_the_success_body(http) -> None:
    http["respond"] = lambda r: httpx.Response(
        200,
        json={"messages": [{"id": "wamid.H", "message_status": "held_for_quality_assessment"}]},
    )
    receipt = await _send(http)
    assert receipt.provider_message_id == "wamid.H"
    assert receipt.message_status == "held_for_quality_assessment"


async def test_a_network_timeout_is_transient(http) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    http["respond"] = boom
    with pytest.raises(transports.TransportUnavailable):
        await _send(http)


# --------------------------------------------------------------------- the dispatch harness


class Wire:
    """What Meta answers, and what reached it."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.json: dict[str, Any] | None = None

    def fail(self, code: int, *, status: int = 400, message: str = "provider said no") -> None:
        self.status = status
        self.json = {"error": {"code": code, "message": message}}

    def succeed(self, body: dict[str, Any] | None = None) -> None:
        self.status = 200
        self.json = body


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    w = Wire()

    def handler(request: httpx.Request) -> httpx.Response:
        w.requests.append(request)
        if w.status != 200:
            return httpx.Response(w.status, json=w.json)
        return httpx.Response(
            200, json=w.json or {"messages": [{"id": f"wamid.{len(w.requests)}"}]}
        )

    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return w


def _cfg(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "email_transport": "log",
        "whatsapp_transport": "whatsapp_cloud",
        "whatsapp_phone_number_id": "999999999999",
        "whatsapp_access_token": SecretStr("platform-token"),
    }
    base.update(overrides)
    return settings().model_copy(update=base)


@pytest.fixture
def cfg() -> Settings:
    return _cfg()


@pytest.fixture
def platform(cfg: Settings) -> dict[str, transports.Transport]:
    return transports.build(cfg)


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    init_engine(settings())
    yield
    await dispose_engine()


def _scope(org: uuid.UUID) -> Any:
    return session_scope(organization_id=org)


async def _connect(org: uuid.UUID, phone: str = PHONE) -> None:
    async with _scope(org) as s:
        s.add(
            ProviderAccount(
                organization_id=org,
                channel="whatsapp",
                waba_id="123456789",
                phone_number_id=phone,
                display_number="+91 99999 00000",
                access_token_enc=pii.seal(TOKEN, kind="generic", settings=settings())[0],
                status="connected",
            )
        )


async def _template(org: uuid.UUID, code: str, status: str) -> None:
    async with _scope(org) as s:
        s.add(
            models.ProviderTemplate(
                organization_id=org,
                code=code,
                channel="whatsapp",
                provider_name=code,
                language="en",
                category="utility",
                status=status,
            )
        )


async def _stage(org: uuid.UUID, *, template_code: str | None = None) -> uuid.UUID:
    async with _scope(org) as s:
        message = await services.stage_message(
            s,
            organization_id=org,
            channel="whatsapp",
            category="operational",
            to_address=TO,
            subject=None,
            body="Your order has shipped.",
            created_by=services.SYSTEM_ACTOR,
            provider_template_code=template_code,
            provider_template_payload=(
                {"name": template_code, "language": {"code": "en"}} if template_code else None
            ),
        )
        assert message is not None
        return message.id


def _envelope(org: uuid.UUID, message_id: uuid.UUID, event_id: uuid.UUID | None = None) -> dict:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": services.EVT_MESSAGE_QUEUED,
        "organization_id": str(org),
        "payload": {"message_id": str(message_id)},
    }


async def _dispatch(
    org: uuid.UUID,
    message_id: uuid.UUID,
    platform: dict[str, transports.Transport],
    cfg: Settings,
    event_id: uuid.UUID | None = None,
) -> None:
    await dispatcher.dispatch(
        _envelope(org, message_id, event_id), transports=platform, cfg=cfg, tenant_route=None
    )


async def _message(org: uuid.UUID, message_id: uuid.UUID) -> Message:
    async with _scope(org) as s:
        message = await selectors.get_message(s, org, message_id)
        assert message is not None
        return message


async def _queued_events(org: uuid.UUID, message_id: uuid.UUID) -> list[OutboxEvent]:
    async with _scope(org) as s:
        rows = (
            (
                await s.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.organization_id == org,
                        OutboxEvent.aggregate_id == message_id,
                        OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED,
                    )
                    .order_by(OutboxEvent.created_at)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest_asyncio.fixture
async def sweep_scopes(worker_engine: None) -> AsyncIterator[tuple[Any, Any]]:
    """`(discovery_factory, work_scope)` wired as `app/cron.py`'s `main()` wires them: discovery on
    the real outbox role, every write tenant-bound on the app role."""
    cfg = settings()
    discovery_engine = create_async_engine(cfg.outbox_dsn, connect_args=_connect_args())
    work_engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)
    work_maker = async_sessionmaker(work_engine, expire_on_commit=False)

    @asynccontextmanager
    async def work_scope(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with work_maker() as s, s.begin():
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(organization_id)},
            )
            yield s

    yield discovery, work_scope
    await discovery_engine.dispose()
    await work_engine.dispose()


# --------------------------------------------------------------------- retry


async def test_a_transient_failure_is_requeued_by_the_sweep_and_then_sent(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)

    wire.fail(130429, status=429, message="throughput reached")
    await _dispatch(org_id, message_id, platform, cfg)

    message = await _message(org_id, message_id)
    assert message.state == STATE_PENDING
    assert message.attempts == 1
    assert message.last_error == "130429: throughput reached"
    assert message.provider_error_code == "130429"
    # Nothing has queued a second event yet: the first was consumed, and only the sweep can.
    assert len(await _queued_events(org_id, message_id)) == 1

    now = message.updated_at
    # Before the backoff has elapsed: nothing to do.
    queued = await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=cfg.dispatch_max_attempts,
        now=now + services.retry_delay(1) - timedelta(seconds=5),
    )
    assert queued == 0

    # After it: exactly one fresh event, with an id of its own.
    later = now + services.retry_delay(1) + timedelta(seconds=1)
    queued = await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=cfg.dispatch_max_attempts,
        now=later,
    )
    assert queued == 1
    events = await _queued_events(org_id, message_id)
    assert len(events) == 2
    assert events[0].id != events[1].id

    # The fresh event is not swallowed by `already_processed`, and this time Meta accepts.
    wire.succeed()
    await _dispatch(org_id, message_id, platform, cfg, event_id=events[1].id)
    sent = await _message(org_id, message_id)
    assert sent.state == STATE_SENT
    assert sent.attempts == 2
    assert len(wire.requests) == 2


async def test_the_original_event_redelivered_is_still_swallowed(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    """The reason the sweep queues a *new* event: the old one hits `already_processed`."""
    await _connect(org_id)
    message_id = await _stage(org_id)
    event_id = uuid.uuid4()
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg, event_id=event_id)
    wire.succeed()
    await _dispatch(org_id, message_id, platform, cfg, event_id=event_id)
    assert len(wire.requests) == 1
    assert (await _message(org_id, message_id)).state == STATE_PENDING


async def test_retries_stop_at_the_cap_and_the_message_fails_with_a_clear_reason(
    org_id, worker_engine, wire, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    cfg = _cfg(dispatch_max_attempts=3)
    platform = transports.build(cfg)
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(2, status=503, message="temporary downtime")

    await _dispatch(org_id, message_id, platform, cfg)
    for _ in range(2):
        message = await _message(org_id, message_id)
        assert message.state == STATE_PENDING
        await services.sweep_message_retries(
            discovery_factory=discovery,
            work_scope=work_scope,
            max_attempts=3,
            now=message.updated_at + services.retry_delay(message.attempts) + timedelta(seconds=1),
        )
        event = (await _queued_events(org_id, message_id))[-1]
        await _dispatch(org_id, message_id, platform, cfg, event_id=event.id)

    final = await _message(org_id, message_id)
    assert final.state == STATE_FAILED
    assert final.attempts == 3
    assert final.last_error == "retries_exhausted after 3 attempts: 2: temporary downtime"
    assert len(wire.requests) == 3

    # Terminal means terminal: another sweep finds nothing, and a failed event was published.
    queued = await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=3,
        now=final.updated_at + timedelta(days=1),
    )
    assert queued == 0
    async with _scope(org_id) as s:
        failed_events = (
            (
                await s.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_FAILED)
                )
            )
            .scalars()
            .all()
        )
        ledger = (await s.execute(select(Delivery).order_by(Delivery.attempt_no))).scalars().all()
    assert len(failed_events) == 1
    assert [d.outcome for d in ledger] == ["failed", "failed", "failed"]


async def test_the_backoff_grows_and_is_capped() -> None:
    delays = [services.retry_delay(n).total_seconds() for n in range(1, 9)]
    assert delays[:5] == [60, 120, 240, 480, 960]
    assert delays == sorted(delays)
    assert delays[-1] == services.RETRY_CAP_SECONDS


async def test_a_second_sweep_tick_does_not_queue_the_same_message_again(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    """The sweep restarts the message's backoff clock, so a backlogged dispatcher is not fed one
    duplicate event per tick."""
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(131016, status=503)
    await _dispatch(org_id, message_id, platform, cfg)
    at = (await _message(org_id, message_id)).updated_at + services.retry_delay(1)
    at += timedelta(seconds=1)

    for tick in range(3):
        await services.sweep_message_retries(
            discovery_factory=discovery,
            work_scope=work_scope,
            max_attempts=6,
            now=at + timedelta(seconds=tick),
        )
    assert len(await _queued_events(org_id, message_id)) == 2  # the original and one retry


async def test_two_queued_events_for_one_message_send_it_once(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    """No double send: `claim_for_send` takes only `pending`, so whichever event loses the race
    finds nothing to claim."""
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)
    at = (await _message(org_id, message_id)).updated_at + services.retry_delay(1)
    await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=6,
        now=at + timedelta(seconds=1),
    )
    retry_event = (await _queued_events(org_id, message_id))[-1]

    wire.succeed()
    await _dispatch(org_id, message_id, platform, cfg, event_id=retry_event.id)
    # A second, distinct event for the same message (a sweep racing a slow dispatcher).
    await _dispatch(org_id, message_id, platform, cfg, event_id=uuid.uuid4())

    assert len(wire.requests) == 2  # the failed attempt and exactly one successful send
    assert (await _message(org_id, message_id)).state == STATE_SENT


async def test_the_sweep_skips_messages_that_are_not_pending(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    sent_id = await _stage(org_id)
    await _dispatch(org_id, sent_id, platform, cfg)
    failed_id = await _stage(org_id)
    wire.fail(131026)
    await _dispatch(org_id, failed_id, platform, cfg)

    queued = await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=6,
        now=datetime.now(UTC) + timedelta(days=1),
    )
    assert queued == 0


async def test_the_sweep_serves_every_tenant_under_its_own_binding(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    org_b = uuid.uuid4()
    await _connect(org_id)
    await _connect(org_b, phone="222222222222")
    a = await _stage(org_id)
    b = await _stage(org_b)
    wire.fail(130429, status=429)
    await _dispatch(org_id, a, platform, cfg)
    await _dispatch(org_b, b, platform, cfg)

    queued = await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=6,
        now=datetime.now(UTC) + timedelta(hours=1),
    )
    assert queued == 2
    for org, message_id in ((org_id, a), (org_b, b)):
        events = await _queued_events(org, message_id)
        assert len(events) == 2
        assert {e.organization_id for e in events} == {org}
        assert {e.payload["organization_id"] for e in events} == {str(org)}


async def test_a_tenant_bound_requeue_cannot_reach_another_tenants_message(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)

    other = uuid.uuid4()
    async with _scope(other) as s:
        outcome = await services.requeue_for_retry(
            s,
            organization_id=other,
            message_id=message_id,
            max_attempts=6,
            now=datetime.now(UTC) + timedelta(days=1),
        )
    assert outcome == "skipped"
    assert len(await _queued_events(org_id, message_id)) == 1


async def test_a_message_left_pending_past_a_lowered_cap_is_failed_by_the_sweep(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)

    await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=1,
        now=datetime.now(UTC) + timedelta(hours=1),
    )
    message = await _message(org_id, message_id)
    assert message.state == STATE_FAILED
    assert message.last_error is not None
    assert message.last_error.startswith("retries_exhausted after 1 attempts")


async def test_the_campaign_drivers_tick_runs_the_retry_sweep(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)

    driver = CampaignDriver(
        discovery_factory=discovery, work_scope=work_scope, dispatch_max_attempts=6
    )
    report = await driver.tick(now=datetime.now(UTC) + timedelta(hours=1))
    assert report.retries_queued == 1
    assert len(await _queued_events(org_id, message_id)) == 2


# --------------------------------------------------------------------- classes through dispatch


@pytest.mark.parametrize(
    "code",
    [131049, 131048, 131026, 131047, 131051, 131050, 131042, 132000, 132007, 132012, 132016],
)
async def test_terminal_codes_fail_at_once_and_keep_the_code(
    code: int, org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(code, message=f"meta says {code}")
    await _dispatch(org_id, message_id, platform, cfg)

    message = await _message(org_id, message_id)
    assert message.state == STATE_FAILED
    assert message.attempts == 1
    assert message.last_error == f"{code}: meta says {code}"
    assert message.provider_error_code == str(code)
    assert len(wire.requests) == 1


@pytest.mark.parametrize("code", [130429, 80007, 131056, 131000, 131016, 133004, 2, 1])
async def test_retryable_codes_stay_pending(
    code: int, org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(code, status=400)
    await _dispatch(org_id, message_id, platform, cfg)
    message = await _message(org_id, message_id)
    assert message.state == STATE_PENDING
    assert message.provider_error_code == str(code)


async def test_a_held_message_is_sent_with_its_status_on_the_ledger(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.succeed(
        {"messages": [{"id": "wamid.HELD", "message_status": "held_for_quality_assessment"}]}
    )
    await _dispatch(org_id, message_id, platform, cfg)

    message = await _message(org_id, message_id)
    assert message.state == STATE_SENT
    assert message.provider_message_id == "wamid.HELD"
    async with _scope(org_id) as s:
        (delivery,) = (await s.execute(select(Delivery))).scalars().all()
    assert delivery.detail == "message_status=held_for_quality_assessment"


# --------------------------------------------------------------------- connection health


async def _health_state(org: uuid.UUID) -> str | None:
    async with _scope(org) as s:
        health = await w1_selectors.get_health(s, org)
        return health.token_state if health is not None else None


async def test_a_rejected_token_fails_the_message_and_flips_the_connections_health(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    first = await _stage(org_id)
    wire.fail(190, message=f"Error validating access token {TOKEN}")
    await _dispatch(org_id, first, platform, cfg)

    message = await _message(org_id, first)
    assert message.state == STATE_FAILED
    assert message.provider_error_code == "190"
    assert message.last_error is not None
    assert message.last_error.startswith("190: Error validating access token")
    assert TOKEN not in message.last_error  # scrubbed by the tenant wrapper, code kept
    assert await _health_state(org_id) == "invalid"

    # The next sends fail fast: no request reaches Meta, and the reason is a code.
    second = await _stage(org_id)
    third = await _stage(org_id)
    await _dispatch(org_id, second, platform, cfg)
    await _dispatch(org_id, third, platform, cfg)
    assert len(wire.requests) == 1
    for message_id in (second, third):
        fast = await _message(org_id, message_id)
        assert fast.state == STATE_FAILED
        assert fast.last_error == dispatcher.TOKEN_INVALID


async def test_reconnecting_clears_the_fast_fail(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    wire.fail(190)
    await _dispatch(org_id, await _stage(org_id), platform, cfg)
    assert await _health_state(org_id) == "invalid"

    async with _scope(org_id) as s:
        await w1_services.record_token_check(s, org_id, state="valid", reset_expiry=True)
    wire.succeed()
    again = await _stage(org_id)
    await _dispatch(org_id, again, platform, cfg)
    assert (await _message(org_id, again)).state == STATE_SENT


async def test_replacing_the_token_clears_the_fast_fail_even_without_a_health_write(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    """`replace_whatsapp_connection` touches the account row, not the health row: an `invalid`
    older than the account's last change describes the previous token."""
    await _connect(org_id)
    wire.fail(190)
    await _dispatch(org_id, await _stage(org_id), platform, cfg)
    assert await _health_state(org_id) == "invalid"

    async with _scope(org_id) as s:
        account = await selectors.get_whatsapp_connection(s, org_id)
        assert account is not None
        account.updated_at = datetime.now(UTC) + timedelta(seconds=5)
    wire.succeed()
    again = await _stage(org_id)
    await _dispatch(org_id, again, platform, cfg)
    assert (await _message(org_id, again)).state == STATE_SENT


@pytest.mark.parametrize("code", [463, 467, 0])
async def test_every_credential_code_flips_health(
    code: int, org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    wire.fail(code)
    message_id = await _stage(org_id)
    await _dispatch(org_id, message_id, platform, cfg)
    assert (await _message(org_id, message_id)).state == STATE_FAILED
    assert await _health_state(org_id) == "invalid"


@pytest.mark.parametrize("code", [368, 3, 10, 200, 250])
async def test_a_policy_block_or_permission_error_fails_only_that_message(
    code: int, org_id, worker_engine, wire, cfg, platform
) -> None:
    """The connection stays usable: health is untouched and the next message is still sent."""
    await _connect(org_id)
    wire.fail(code, message="blocked for this call")
    first = await _stage(org_id)
    await _dispatch(org_id, first, platform, cfg)

    message = await _message(org_id, first)
    assert message.state == STATE_FAILED
    assert message.provider_error_code == str(code)
    assert message.last_error == f"{code}: blocked for this call"
    assert await _health_state(org_id) is None

    wire.succeed()
    second = await _stage(org_id)
    await _dispatch(org_id, second, platform, cfg)
    assert (await _message(org_id, second)).state == STATE_SENT
    assert len(wire.requests) == 2


async def test_a_message_level_refusal_does_not_touch_health(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    wire.fail(131026)
    await _dispatch(org_id, await _stage(org_id), platform, cfg)
    assert await _health_state(org_id) is None


async def test_the_platforms_own_sender_is_never_marked_invalid(
    worker_engine, wire, cfg, platform
) -> None:
    """The platform tenant has no connection and no health row; its message fails, nothing else."""
    platform_org = uuid.UUID(int=0)
    message_id = await _stage(platform_org)
    wire.fail(190)
    await _dispatch(platform_org, message_id, platform, cfg)
    assert (await _message(platform_org, message_id)).state == STATE_FAILED
    assert await _health_state(platform_org) is None


# --------------------------------------------------------------------- template re-check


async def test_an_approved_template_still_sends(org_id, worker_engine, wire, cfg, platform) -> None:
    await _connect(org_id)
    await _template(org_id, "order_shipped", "approved")
    message_id = await _stage(org_id, template_code="order_shipped")
    await _dispatch(org_id, message_id, platform, cfg)
    assert (await _message(org_id, message_id)).state == STATE_SENT
    assert len(wire.requests) == 1


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        ("paused", "132015", "template_paused"),
        ("disabled", "132016", "template_disabled"),
        ("rejected", "132001", "template_not_found_or_not_approved"),
        ("pending", "132001", "template_not_found_or_not_approved"),
    ],
)
async def test_a_template_that_stopped_being_sendable_after_staging_fails_before_the_send(
    status: str, code: str, reason: str, org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    await _template(org_id, "order_shipped", "approved")
    message_id = await _stage(org_id, template_code="order_shipped")
    async with _scope(org_id) as s:
        row = await selectors.get_provider_template(s, org_id, "order_shipped", "whatsapp")
        assert row is not None
        row.status = status

    await _dispatch(org_id, message_id, platform, cfg)

    message = await _message(org_id, message_id)
    assert message.state == STATE_FAILED
    assert message.provider_error_code == code
    assert message.last_error is not None
    assert message.last_error.startswith(f"{code}: {reason}")
    assert wire.requests == []  # never reached Meta


async def test_a_deleted_template_fails_before_the_send(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    await _template(org_id, "order_shipped", "approved")
    message_id = await _stage(org_id, template_code="order_shipped")
    async with _scope(org_id) as s:
        row = await selectors.get_provider_template(s, org_id, "order_shipped", "whatsapp")
        assert row is not None
        row.deleted_at = datetime.now(UTC)

    await _dispatch(org_id, message_id, platform, cfg)
    message = await _message(org_id, message_id)
    assert message.state == STATE_FAILED
    assert message.provider_error_code == "132001"
    assert wire.requests == []


async def test_a_retry_after_the_template_was_paused_fails_instead_of_sending(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    await _template(org_id, "order_shipped", "approved")
    message_id = await _stage(org_id, template_code="order_shipped")
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)
    async with _scope(org_id) as s:
        row = await selectors.get_provider_template(s, org_id, "order_shipped", "whatsapp")
        assert row is not None
        row.status = "paused"

    await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=6,
        now=datetime.now(UTC) + timedelta(hours=1),
    )
    event = (await _queued_events(org_id, message_id))[-1]
    wire.succeed()
    await _dispatch(org_id, message_id, platform, cfg, event_id=event.id)

    message = await _message(org_id, message_id)
    assert message.state == STATE_FAILED
    assert message.provider_error_code == "132015"
    assert len(wire.requests) == 1  # only the first, failed, attempt ever reached Meta


# --------------------------------------------------------------------- retry window


async def _set_created_at(s: AsyncSession, message_id: uuid.UUID, at: datetime) -> None:
    # `updated_at` is pinned to itself: it is the retry clock and `onupdate` would move it.
    await s.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(created_at=at, updated_at=Message.updated_at)
    )


async def _age(org: uuid.UUID, message_id: uuid.UUID, *, created_ago: timedelta) -> None:
    """Make a staged-and-failed message look `created_ago` old, as a row left by an earlier
    release would."""
    async with _scope(org) as s:
        await _set_created_at(s, message_id, datetime.now(UTC) - created_ago)


async def test_a_stale_pending_message_is_failed_and_never_sent(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)
    await _age(org_id, message_id, created_ago=timedelta(days=9))
    sent_before = len(wire.requests)

    queued = await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=6,
        now=datetime.now(UTC) + timedelta(hours=1),
        max_age=timedelta(hours=6),
    )

    assert queued == 0
    message = await _message(org_id, message_id)
    assert message.state == STATE_FAILED
    assert message.last_error is not None
    assert message.last_error.startswith("retry_window_expired after 1 attempts")
    assert len(await _queued_events(org_id, message_id)) == 1  # nothing was re-queued
    assert len(wire.requests) == sent_before
    async with _scope(org_id) as s:
        failed_events = (
            (
                await s.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_FAILED)
                )
            )
            .scalars()
            .all()
        )
    assert len(failed_events) == 1


async def test_a_fresh_pending_message_is_still_retried(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)
    await _age(org_id, message_id, created_ago=timedelta(hours=2))

    queued = await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=6,
        now=datetime.now(UTC) + timedelta(minutes=5),
        max_age=timedelta(hours=6),
    )
    assert queued == 1
    assert (await _message(org_id, message_id)).state == STATE_PENDING


async def test_the_retry_window_boundary(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    """Exactly at the window the message is expired; a second inside it, it is retried."""
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    wire.fail(130429, status=429)
    inside, edge = await _stage(org_id), await _stage(org_id)
    await _dispatch(org_id, inside, platform, cfg)
    await _dispatch(org_id, edge, platform, cfg)

    window = timedelta(hours=6)
    now = datetime.now(UTC) + timedelta(hours=1)
    async with _scope(org_id) as s:
        for message_id, created in (
            (inside, now - window + timedelta(seconds=1)),
            (edge, now - window),
        ):
            await _set_created_at(s, message_id, created)

    queued = await services.sweep_message_retries(
        discovery_factory=discovery,
        work_scope=work_scope,
        max_attempts=6,
        now=now,
        max_age=window,
    )
    assert queued == 1
    assert (await _message(org_id, inside)).state == STATE_PENDING
    assert len(await _queued_events(org_id, inside)) == 2
    assert (await _message(org_id, edge)).state == STATE_FAILED


async def test_expiry_is_bounded_per_tick(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovery, work_scope = sweep_scopes
    monkeypatch.setattr(services, "EXPIRE_BATCH", 2)
    await _connect(org_id)
    wire.fail(130429, status=429)
    ids = [await _stage(org_id) for _ in range(3)]
    for message_id in ids:
        await _dispatch(org_id, message_id, platform, cfg)
        await _age(org_id, message_id, created_ago=timedelta(days=3))

    async def tick() -> None:
        await services.sweep_message_retries(
            discovery_factory=discovery,
            work_scope=work_scope,
            max_attempts=6,
            now=datetime.now(UTC) + timedelta(hours=1),
            max_age=timedelta(hours=6),
        )

    await tick()
    states = [(await _message(org_id, m)).state for m in ids]
    assert states.count(STATE_FAILED) == 2
    await tick()
    assert [(await _message(org_id, m)).state for m in ids] == [STATE_FAILED] * 3


async def test_the_expiry_write_is_tenant_bound(org_id, worker_engine, wire, cfg, platform) -> None:
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)
    await _age(org_id, message_id, created_ago=timedelta(days=3))

    other = uuid.uuid4()
    async with _scope(other) as s:
        outcome = await services.expire_stale_retry(
            s,
            organization_id=other,
            message_id=message_id,
            now=datetime.now(UTC),
            max_age=timedelta(hours=6),
        )
    assert outcome == "skipped"
    assert (await _message(org_id, message_id)).state == STATE_PENDING


async def test_the_drivers_tick_expires_stale_retries_with_the_configured_window(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)
    await _age(org_id, message_id, created_ago=timedelta(minutes=30))

    driver = CampaignDriver(
        discovery_factory=discovery,
        work_scope=work_scope,
        dispatch_max_attempts=6,
        dispatch_retry_max_age_seconds=600,
    )
    report = await driver.tick(now=datetime.now(UTC) + timedelta(hours=1))
    assert report.retries_queued == 0
    assert (await _message(org_id, message_id)).state == STATE_FAILED


def test_the_retry_window_defaults_to_six_hours() -> None:
    assert Settings.model_fields["dispatch_retry_max_age_seconds"].default == 6 * 3600


# --------------------------------------------------------------------- claim and re-queue races


async def test_a_second_claim_of_the_same_message_returns_none(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    await _connect(org_id)
    message_id = await _stage(org_id)
    async with _scope(org_id) as s:
        first = await services.claim_for_send(s, organization_id=org_id, message_id=message_id)
    assert first is not None and first.attempts == 1
    async with _scope(org_id) as s:
        second = await services.claim_for_send(s, organization_id=org_id, message_id=message_id)
    assert second is None
    assert (await _message(org_id, message_id)).attempts == 1


async def test_two_concurrent_claims_let_exactly_one_through(
    org_id, worker_engine, wire, cfg, platform
) -> None:
    """The claim read takes the row lock, so the second transaction waits for the first to commit
    and then sees `sending`."""
    await _connect(org_id)
    message_id = await _stage(org_id)
    first_claimed = asyncio.Event()
    release = asyncio.Event()

    async def first() -> Message | None:
        async with _scope(org_id) as s:
            message = await services.claim_for_send(
                s, organization_id=org_id, message_id=message_id
            )
            first_claimed.set()
            await release.wait()  # hold the transaction (and the lock) open
            return message

    async def second() -> Message | None:
        await first_claimed.wait()
        async with _scope(org_id) as s:
            return await services.claim_for_send(s, organization_id=org_id, message_id=message_id)

    t1 = asyncio.create_task(first())
    t2 = asyncio.create_task(second())
    await first_claimed.wait()
    await asyncio.sleep(0.3)
    assert not t2.done()  # blocked on the lock, not racing past it
    release.set()
    won, lost = await asyncio.gather(t1, t2)
    assert won is not None and lost is None
    assert (await _message(org_id, message_id)).attempts == 1


async def test_a_message_awaiting_its_retry_event_is_not_queued_again_for_five_minutes(
    org_id, worker_engine, wire, cfg, platform, sweep_scopes
) -> None:
    discovery, work_scope = sweep_scopes
    await _connect(org_id)
    message_id = await _stage(org_id)
    wire.fail(130429, status=429)
    await _dispatch(org_id, message_id, platform, cfg)
    failed_at = (await _message(org_id, message_id)).updated_at

    async def tick(after: timedelta) -> int:
        return await services.sweep_message_retries(
            discovery_factory=discovery,
            work_scope=work_scope,
            max_attempts=6,
            now=failed_at + after,
            max_age=timedelta(hours=6),
        )

    first_at = services.retry_delay(1) + timedelta(seconds=1)
    assert await tick(first_at) == 1
    # The event is still in the broker: nothing more for 5 minutes after the re-queue,
    # although the backoff itself is only a minute.
    assert await tick(first_at + timedelta(minutes=2)) == 0
    assert await tick(first_at + timedelta(minutes=4, seconds=59)) == 0
    assert len(await _queued_events(org_id, message_id)) == 2
    # Past it, a message still pending (the event was lost) is queued again.
    assert await tick(first_at + timedelta(minutes=5, seconds=1)) == 1
    assert len(await _queued_events(org_id, message_id)) == 3
