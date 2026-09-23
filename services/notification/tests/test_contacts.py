"""Resolving a phone number into a contact id — without a network, and without customer-service.

The reverse of `test_targets.py`, and the interesting behaviour is the opposite shape: everything
short of a clean match degrades to `None` rather than raising. See `app/contacts.py`'s module
docstring for why this is right here and wrong in `targets.py` — a conversation is already
committed by the time this runs, so there is nothing owed that a retry would deliver.
"""

from __future__ import annotations

import httpx
import pytest

from app import contacts
from app.config import Settings


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
    return await contacts.resolve_contact_by_phone(
        _settings(), "11111111-1111-4111-8111-111111111111", "+917976603446"
    )


async def test_a_matching_number_resolves_to_a_contact_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"contact_id": "c0ffee"})

    assert await _resolve(handler, monkeypatch) == "c0ffee"


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


async def test_a_stranger_resolves_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary — a stranger messaging the business, not an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"contact_id": None})

    assert await _resolve(handler, monkeypatch) is None


@pytest.mark.parametrize("status_code", [402, 404, 422, 500, 503])
async def test_anything_short_of_200_degrades_to_none(
    status_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not entitled, not deployed, a rejected token, a 5xx — every one of them degrades the same
    way here, unlike `targets.py`'s split between skip and retry. The conversation is already
    committed; there is nothing a retry would still owe."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "nope"})

    assert await _resolve(handler, monkeypatch) is None


async def test_an_unreachable_customer_service_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    assert await _resolve(handler, monkeypatch) is None


async def test_a_timeout_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    assert await _resolve(handler, monkeypatch) is None
