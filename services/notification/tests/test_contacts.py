"""Resolving a phone number into a contact id — without a network, and without customer-service.

The reverse of `test_targets.py`, and the interesting behaviour is the opposite shape: everything
short of a clean match degrades to `ContactResolution(answered=False)` rather than raising. See
`app/contacts.py`'s module docstring for why this is right here and wrong in `targets.py` — a
conversation is already committed by the time this runs, so there is nothing owed that a retry
would deliver — and for why "no match" and "could not ask" are kept apart rather than both
collapsing to `None` the way an earlier version of this module did.
"""

from __future__ import annotations

import httpx
import pytest

from app import contacts
from app.config import Settings
from app.contacts import ContactResolution


def _settings() -> Settings:
    return Settings(customer_internal_url="http://customer.invalid")


async def _resolve(handler, monkeypatch: pytest.MonkeyPatch) -> ContactResolution:
    """Drive the real resolver against a stubbed transport — no socket is opened."""
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return await contacts.resolve_contact_by_phone(
        _settings(), "11111111-1111-4111-8111-111111111111", "+917976603446"
    )


async def test_a_matching_number_resolves_to_a_contact_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"contact_id": "c0ffee"})

    resolution = await _resolve(handler, monkeypatch)
    assert resolution == ContactResolution(answered=True, contact_id="c0ffee")


async def test_the_request_carries_the_number_in_the_body_not_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Internal-Token")
        seen["path"] = request.url.path
        seen["query"] = str(request.url.params)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"contact_id": None})

    await _resolve(handler, monkeypatch)
    assert seen["token"]
    assert seen["path"].endswith("/contacts:resolve")  # type: ignore[union-attr]
    assert "917976603446" not in str(seen["query"])
    assert "917976603446" in str(seen["body"])


async def test_a_stranger_is_a_definite_answer_of_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary — a stranger messaging the business, not an error, and **answered**: this is not
    the same outcome as customer-service being unreachable, and a caller must be able to tell
    them apart (`test_consumer_inbound.py` is where that distinction matters)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"contact_id": None})

    resolution = await _resolve(handler, monkeypatch)
    assert resolution == ContactResolution(answered=True, contact_id=None)


@pytest.mark.parametrize("status_code", [402, 404, 422, 500, 503])
async def test_anything_short_of_200_is_unanswered(
    status_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not entitled, not deployed, a rejected token, a 5xx — every one of them is "could not ask",
    unlike `targets.py`'s split between skip and retry. The conversation is already committed;
    there is nothing a retry would still owe, but "could not ask" must still not be confused with
    a definite "the answer is nobody"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "nope"})

    resolution = await _resolve(handler, monkeypatch)
    assert resolution == ContactResolution(answered=False)


async def test_an_unparseable_body_is_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 that is not JSON is not an answer either — reading `contact_id` off it would crash
    the caller on a body it cannot control."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    resolution = await _resolve(handler, monkeypatch)
    assert resolution == ContactResolution(answered=False)


async def test_a_json_array_body_is_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 whose body is not even an object — `.get("contact_id")` would crash on a list."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    resolution = await _resolve(handler, monkeypatch)
    assert resolution == ContactResolution(answered=False)


async def test_an_unreachable_customer_service_is_unanswered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    resolution = await _resolve(handler, monkeypatch)
    assert resolution == ContactResolution(answered=False)


async def test_a_timeout_is_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    resolution = await _resolve(handler, monkeypatch)
    assert resolution == ContactResolution(answered=False)


async def test_an_organization_id_that_is_not_a_uuid_asks_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never interpolated into the path raw: a `..` in it would retarget the call. Not an
    answer either, so the conversation keeps whatever it already held."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        requests.append(request)
        return httpx.Response(200, json={"contact_id": None})

    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    resolution = await contacts.resolve_contact_by_phone(
        _settings(), "../../11111111-1111-4111-8111-111111111111", "+917976603446"
    )
    assert resolution == ContactResolution(answered=False)
    assert requests == []
