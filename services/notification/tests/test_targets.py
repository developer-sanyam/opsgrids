"""Resolving a contact into an address — without a network, and without customer-service.

The behaviour worth protecting is the distinction between two failures that look alike and must
not be treated alike: a contact who has no number (skip, forever) and a customer-service that
cannot answer (retry, because the notification is still owed). Collapsing them either spams a
contact who cannot be reached or silently drops mail during an outage.
"""

from __future__ import annotations

import httpx
import jwt
import pytest

from app import targets
from app.config import Settings

ORG_ID = "11111111-1111-4111-8111-111111111111"


def _settings() -> Settings:
    return Settings(customer_internal_url="http://customer.invalid")


async def _resolve(handler, monkeypatch: pytest.MonkeyPatch) -> str | None:
    """Drive the real resolver against a stubbed transport — no socket is opened."""
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return await targets.resolve_contact_target(_settings(), ORG_ID, "c0ffee", "whatsapp")


# --------------------------------------------------------------------- the happy path


async def test_an_address_comes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"contact_id": "c0ffee", "channel": "whatsapp", "phone": "+917976603446"}
        )

    assert await _resolve(handler, monkeypatch) == "+917976603446"


async def test_the_request_carries_a_token_and_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addressed to customer by name, so it cannot be replayed against another service."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Internal-Token")
        seen["channel"] = request.url.params.get("channel")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"phone": "+917976603446"})

    await _resolve(handler, monkeypatch)
    assert seen["token"]
    assert seen["channel"] == "whatsapp"
    assert "/internal/v1/organizations/" in str(seen["path"])
    assert "/messaging-target" in str(seen["path"])


async def test_the_token_names_the_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `org` claim says which organization the call is about. Customer does not check it on
    this route yet; it has to be on the token before it can be checked without locking this
    caller out."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Internal-Token")
        return httpx.Response(200, json={"phone": "+917976603446"})

    await _resolve(handler, monkeypatch)
    claims = jwt.decode(
        str(seen["token"]),
        _settings().internal_service_token.get_secret_value(),
        algorithms=["HS256"],
        audience="customer",
    )
    assert claims["iss"] == "notification"
    assert claims.get("org") == ORG_ID


# --------------------------------------------------------------------- permanent: skip


async def test_a_contact_with_no_number_resolves_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary, and most contacts are like this. Retrying would never help."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"contact_id": "c0ffee", "phone": None})

    assert await _resolve(handler, monkeypatch) is None


async def test_an_unknown_contact_resolves_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 covers "not in this organization", which customer reports rather than 403 — telling
    a caller an id exists elsewhere is itself a disclosure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    assert await _resolve(handler, monkeypatch) is None


async def test_a_refusal_resolves_to_none_rather_than_retrying_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 4xx that is not 404 is our mistake — a bad channel, a rejected token. Retrying a
    malformed request forever only hides it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "not a channel"})

    assert await _resolve(handler, monkeypatch) is None


# --------------------------------------------------------------------- transient: retry


async def test_a_server_error_raises_so_the_event_redelivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The important one. A notification is still owed, so this must not become a skip."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(targets.TargetUnavailable):
        await _resolve(handler, monkeypatch)


async def test_an_unreachable_customer_service_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(targets.TargetUnavailable):
        await _resolve(handler, monkeypatch)


async def test_a_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(targets.TargetUnavailable):
        await _resolve(handler, monkeypatch)
