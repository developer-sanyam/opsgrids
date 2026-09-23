"""Verifying an assignment target against account-service — without a network.

`membership_is_active` gates a write (`services.assign_conversation`), not a display fact, so
unlike `app/contacts.py`'s degrade-to-unknown it fails **closed**: anything short of a confirmed,
active membership in the caller's own organization is `False`.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.memberships import membership_is_active

ORG_ID = "11111111-1111-4111-8111-111111111111"
MEMBERSHIP_ID = "22222222-2222-4222-8222-222222222222"


def _settings() -> Settings:
    return Settings(account_internal_url="http://account.invalid")


async def _check(handler, monkeypatch: pytest.MonkeyPatch) -> bool:
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return await membership_is_active(_settings(), ORG_ID, MEMBERSHIP_ID)


async def test_a_confirmed_active_membership_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            f"/internal/v1/organizations/{ORG_ID}/memberships/{MEMBERSHIP_ID}"
        )
        return httpx.Response(200, json={"exists": True, "active": True})

    assert await _check(handler, monkeypatch) is True


async def test_it_carries_a_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Internal-Token")
        return httpx.Response(200, json={"exists": True, "active": True})

    await _check(handler, monkeypatch)
    assert seen["token"]


async def test_a_membership_that_exists_but_is_not_active_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"exists": True, "active": False})

    assert await _check(handler, monkeypatch) is False


async def test_a_membership_that_does_not_exist_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"exists": False, "active": False})

    assert await _check(handler, monkeypatch) is False


@pytest.mark.parametrize("status_code", [401, 404, 422, 500, 503])
async def test_anything_short_of_200_is_false(
    status_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails closed: an assignment target this service cannot confirm must never be treated as
    good, unlike `app/contacts.py`'s degrade-to-unknown for a display fact."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "nope"})

    assert await _check(handler, monkeypatch) is False


async def test_an_unparseable_body_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    assert await _check(handler, monkeypatch) is False


async def test_an_unreachable_account_service_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    assert await _check(handler, monkeypatch) is False
