"""Slice W1 without a database: the pure mappings, the Graph client, the config helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app import w1_domain, w1_services
from app.config import Settings, graph_version, meta_app_secret
from app.w1_graph import GraphError, MetaGraph
from app.w1_models import SetupRun
from tests.w1_helpers import APP_ID, APP_SECRET, CODE, TOKEN


def cfg(**overrides: Any) -> Settings:
    return Settings(  # type: ignore[arg-type]
        meta_app_id=APP_ID, meta_app_secret=SecretStr(APP_SECRET), **overrides
    )


# --------------------------------------------------------------------- domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GREEN", "green"),
        ("yellow", "yellow"),
        ("RED", "red"),
        ("NA", "unknown"),
        (None, "unknown"),
    ],
)
def test_quality_maps_and_defaults_to_unknown(raw: object, expected: str) -> None:
    assert w1_domain.quality_from_meta(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TIER_250", "TIER_250"),
        ("TIER_1K", "TIER_1K"),
        ("TIER_100K", "TIER_100K"),
        ("TIER_UNLIMITED", "UNLIMITED"),
        ("TIER_50", "unknown"),
        (None, "unknown"),
    ],
)
def test_limits_map_and_an_unfamiliar_tier_is_unknown(raw: object, expected: str) -> None:
    assert w1_domain.limit_from_meta(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("APPROVED", "approved"),
        ("AVAILABLE_WITHOUT_REVIEW", "approved"),
        ("PENDING_REVIEW", "pending_review"),
        ("DECLINED", "declined"),
        ("NONE", "none"),
        ("WHATEVER", "none"),
    ],
)
def test_name_status_maps(raw: str, expected: str) -> None:
    assert w1_domain.name_status_from_meta(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CONNECTED", "connected"),
        ("PENDING", "pending_verification"),
        ("UNVERIFIED", "pending_verification"),
        ("DISCONNECTED", "disconnected"),
        ("BANNED", "disconnected"),
        (None, "disconnected"),
    ],
)
def test_number_status_maps(raw: object, expected: str) -> None:
    assert w1_domain.status_from_meta(raw) == expected


def test_display_numbers_compare_by_digits() -> None:
    assert w1_domain.digits("+91 98765-43210") == w1_domain.digits("919876543210")


def test_token_state_follows_the_stored_state_and_the_expiry() -> None:
    now = datetime.now(UTC)
    assert w1_domain.token_state("valid", expires_at=None, now=now) == "valid"
    assert w1_domain.token_state("valid", expires_at=now + timedelta(days=2), now=now) == "expiring"
    assert w1_domain.token_state("valid", expires_at=now - timedelta(days=1), now=now) == "invalid"
    assert w1_domain.token_state("valid", expires_at=now + timedelta(days=90), now=now) == "valid"
    assert w1_domain.token_state("invalid", expires_at=None, now=now) == "invalid"
    assert w1_domain.token_state("bogus", expires_at=None, now=now) == "unknown"


def test_webhook_state_is_unverified_until_something_is_known() -> None:
    now = datetime.now(UTC)
    assert w1_domain.webhook_state(subscribed_at=None, last_event_at=None, now=now) == "unverified"
    assert w1_domain.webhook_state(subscribed_at=now, last_event_at=None, now=now) == "silent"
    recent = now - timedelta(hours=3)
    assert w1_domain.webhook_state(subscribed_at=None, last_event_at=recent, now=now) == "receiving"
    old = now - timedelta(days=30)
    assert w1_domain.webhook_state(subscribed_at=now, last_event_at=old, now=now) == "silent"


def test_a_run_stuck_running_is_reported_timed_out_by_a_read() -> None:
    run = SetupRun(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        status="running",
        steps=w1_services.initial_steps(),
        code_hash="x",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    shown = w1_services.run_out(run)
    assert (shown.status, shown.error_code) == ("failed", "timed_out")
    assert run.status == "running"  # a read writes nothing


# --------------------------------------------------------------------- config


def test_the_graph_version_can_be_overridden_and_defaults_to_the_base() -> None:
    assert graph_version(cfg()) == "v25.0"
    pinned = cfg(whatsapp_graph_version="v23.0")
    assert graph_version(pinned) == "v23.0"
    assert pinned.whatsapp_api_base == "https://graph.facebook.com/v23.0"


def test_the_app_secret_falls_back_to_the_existing_webhook_secret() -> None:
    assert meta_app_secret(Settings(whatsapp_app_secret=SecretStr("legacy"))) == "legacy"  # type: ignore[arg-type]
    assert meta_app_secret(cfg()) == APP_SECRET


def test_signup_is_disabled_until_all_three_keys_are_set() -> None:
    assert not w1_services.signup_enabled(Settings())  # type: ignore[call-arg]
    assert not w1_services.signup_enabled(cfg())  # no configuration id
    full = cfg(meta_embedded_signup_config_id="c1")
    assert w1_services.signup_enabled(full)
    assert w1_services.signup_config(full).app_id == APP_ID


# --------------------------------------------------------------------- graph client


async def test_the_code_exchange_sends_the_secret_only_on_that_call_and_returns_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": TOKEN})

    graph = MetaGraph(cfg(), transport=httpx.MockTransport(handler))
    assert await graph.exchange_code(CODE) == TOKEN
    (request,) = seen
    assert request.url.path.endswith("/oauth/access_token")
    assert request.url.params["client_secret"] == APP_SECRET
    assert "authorization" not in request.headers


async def test_an_exchange_without_a_configured_app_is_refused_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be made")

    graph = MetaGraph(Settings(), transport=httpx.MockTransport(handler))  # type: ignore[call-arg]
    with pytest.raises(GraphError):
        await graph.exchange_code(CODE)


async def test_a_refusal_never_puts_the_secret_or_the_code_in_the_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": 100, "message": f"bad {APP_SECRET} {CODE}"}}
        return httpx.Response(400, json=body)

    graph = MetaGraph(cfg(), transport=httpx.MockTransport(handler))
    with pytest.raises(GraphError) as caught:
        await graph.exchange_code(CODE)
    text = f"{caught.value} {caught.value.public_message}"
    assert APP_SECRET not in text and CODE not in text


async def test_an_unreachable_meta_is_retryable_and_says_nothing_about_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    graph = MetaGraph(cfg(), transport=httpx.MockTransport(handler))
    with pytest.raises(GraphError) as caught:
        await graph.subscribe_app("9001", TOKEN)
    assert caught.value.retryable is True
    assert "graph.facebook.com" not in caught.value.public_message


async def test_numbers_are_paged_by_cursor_and_a_next_url_is_never_followed() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.params.get("after") == "c1":
            return httpx.Response(200, json={"data": [{"id": "2"}]})
        return httpx.Response(
            200,
            json={
                "data": [{"id": "1"}],
                "paging": {"cursors": {"after": "c1"}, "next": "https://evil.example/x"},
            },
        )

    graph = MetaGraph(cfg(), transport=httpx.MockTransport(handler))
    numbers = await graph.list_numbers("9001", TOKEN)
    assert [n["id"] for n in numbers] == ["1", "2"]
    assert {c.url.host for c in calls} == {"graph.facebook.com"}
    assert all(c.headers["authorization"] == f"Bearer {TOKEN}" for c in calls)


async def test_a_token_meta_refuses_is_invalid_but_an_outage_is_not_an_answer() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 190, "message": "x"}})

    def outage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    refused = MetaGraph(cfg(), transport=httpx.MockTransport(refuse))
    assert await refused.token_is_valid(TOKEN) is False
    down = MetaGraph(cfg(), transport=httpx.MockTransport(outage))
    with pytest.raises(GraphError):
        await down.token_is_valid(TOKEN)


def test_wabas_are_read_from_the_granted_scopes_without_repeats() -> None:
    data = {
        "granular_scopes": [
            {"scope": "whatsapp_business_management", "target_ids": ["1", "2"]},
            {"scope": "whatsapp_business_messaging", "target_ids": ["2"]},
            {"scope": "business_management", "target_ids": ["9"]},
            "junk",
        ]
    }
    assert MetaGraph.wabas_from_debug(data) == ["1", "2"]
    assert MetaGraph.wabas_from_debug({}) == []


# --------------------------------------------------------------------- registration vocabulary


@pytest.mark.parametrize(
    ("status", "platform", "expected"),
    [
        ("connected", "CLOUD_API", "registered"),
        ("pending_verification", "NOT_APPLICABLE", "not_registered"),
        ("connected", None, "registered"),
        ("pending_verification", None, "not_registered"),
        ("disconnected", None, "unknown"),
    ],
)
def test_registration_state_reads_platform_type_then_status(
    status: str, platform: str | None, expected: str
) -> None:
    assert w1_domain.registration_state(status, platform) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [({"level": "high"}, "HIGH"), ("STANDARD", "STANDARD"), ({"level": ["x"]}, None), (5, None)],
)
def test_throughput_is_read_from_the_level_and_ignores_junk(
    raw: object, expected: str | None
) -> None:
    assert w1_domain.throughput_from_meta(raw) == expected


def test_status_word_is_sanitised_for_a_sentence() -> None:
    assert w1_domain.status_word("NOT_VERIFIED") == "not verified"
    assert w1_domain.status_word("a b; drop") == "unknown"
    assert w1_domain.status_word(None) == "unknown"


async def test_register_sends_the_pin_in_the_body_never_the_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True})

    graph = MetaGraph(cfg(), transport=httpx.MockTransport(handler))
    await graph.register_number("1001", TOKEN, "123456")
    (request,) = seen
    assert request.method == "POST" and request.url.path.endswith("/1001/register")
    assert "123456" not in str(request.url) and TOKEN not in str(request.url)
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.read() == b'{"messaging_product":"whatsapp","pin":"123456"}'


async def test_a_success_false_from_register_is_a_refusal() -> None:
    graph = MetaGraph(
        cfg(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"success": False}))
    )
    with pytest.raises(GraphError):
        await graph.register_number("1001", TOKEN, "123456")
