"""Resolving a contact into an address — without a network, and without customer-service.

The behaviour worth protecting is the distinction between two failures that look alike and must
not be treated alike: a contact who has no number (skip, forever) and a customer-service that
cannot answer (retry, because the notification is still owed). Collapsing them either spams a
contact who cannot be reached or silently drops mail during an outage.
"""

from __future__ import annotations

import httpx
import pytest

from app import targets
from app.config import Settings
from tests.helpers import CapturingLog

ORG_ID = "11111111-1111-4111-8111-111111111111"
CONTACT_ID = "22222222-2222-4222-8222-222222222222"
#: Another organization's ids: what a crafted payload field would try to reach.
VICTIM_ORG_ID = "33333333-3333-4333-8333-333333333333"
VICTIM_CONTACT_ID = "44444444-4444-4444-8444-444444444444"


def _settings() -> Settings:
    return Settings(customer_internal_url="http://customer.invalid")


async def _resolve(
    handler,
    monkeypatch: pytest.MonkeyPatch,
    *,
    organization_id: str = ORG_ID,
    contact_id: str = CONTACT_ID,
) -> str | None:
    """Drive the real resolver against a stubbed transport — no socket is opened."""
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return await targets.resolve_contact_target(
        _settings(), organization_id, contact_id, "whatsapp"
    )


# --------------------------------------------------------------------- the happy path


async def test_an_address_comes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"contact_id": CONTACT_ID, "channel": "whatsapp", "phone": "+917976603446"}
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


# --------------------------------------------------------------------- permanent: skip


async def test_a_contact_with_no_number_resolves_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary, and most contacts are like this. Retrying would never help."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"contact_id": CONTACT_ID, "phone": None})

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


# --------------------------------------------------------------------- ids never reach a URL raw
#
# `contact_id` comes out of an event payload at a path a tenant admin chose, so it is whatever the
# tenant's own users typed into some field. `httpx` resolves `..` in an absolute URL before it
# sends, so an id interpolated as-is walks the call onto another organization's contact under a
# token customer-service accepts for any organization.

#: Every shape that would change which route is called, or whose, if it reached the path.
_HOSTILE_IDS = [
    f"../../{VICTIM_ORG_ID}/contacts/{VICTIM_CONTACT_ID}",
    f"..%2F..%2F{VICTIM_ORG_ID}%2Fcontacts%2F{VICTIM_CONTACT_ID}",
    f"{CONTACT_ID}/../../../{VICTIM_ORG_ID}/contacts/{VICTIM_CONTACT_ID}",
    f"{CONTACT_ID}?x=1",
    f"{CONTACT_ID}#frag",
    "c0ffee",
    "",
    "   ",
]


@pytest.mark.parametrize("contact_id", _HOSTILE_IDS)
async def test_a_contact_id_that_is_not_a_uuid_makes_no_request(
    contact_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression for the path traversal: nothing is sent at all, and the answer is the
    permanent "no address" (None), not an outage — retrying a value that is not a contact id
    would never help."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        requests.append(request)
        return httpx.Response(200, json={"phone": "+917976603446"})

    assert await _resolve(handler, monkeypatch, contact_id=contact_id) is None
    assert requests == []


@pytest.mark.parametrize("organization_id", ["../x", "not-a-uuid", f"{ORG_ID}/../{VICTIM_ORG_ID}"])
async def test_an_organization_id_that_is_not_a_uuid_makes_no_request(
    organization_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        requests.append(request)
        return httpx.Response(200, json={"phone": "+917976603446"})

    assert await _resolve(handler, monkeypatch, organization_id=organization_id) is None
    assert requests == []


async def test_the_request_path_is_exactly_the_one_route_for_these_two_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never a path with a `..` in it, and never anything but the caller's own organization —
    the organization in the path is the event's, which no payload field can change."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"phone": "+917976603446"})

    await _resolve(handler, monkeypatch)
    assert seen == [
        f"/internal/v1/organizations/{ORG_ID}/contacts/{CONTACT_ID}/messaging-target"
        "?channel=whatsapp"
    ]


async def test_a_braced_uppercase_uuid_is_sent_in_canonical_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A UUID is accepted in the spellings `uuid.UUID` parses, but the URL is built from the
    parsed value — none of the spelling variants reaches the wire."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"phone": "+917976603446"})

    await _resolve(handler, monkeypatch, contact_id="{" + CONTACT_ID.upper() + "}")
    assert seen == [f"/internal/v1/organizations/{ORG_ID}/contacts/{CONTACT_ID}/messaging-target"]


async def test_a_refused_id_is_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The value is exactly the field somebody else controls — the warning names the channel
    and which id was bad, never the id."""
    captured = CapturingLog()
    monkeypatch.setattr(targets, "log", captured)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        return httpx.Response(200, json={})

    hostile = f"../../{VICTIM_ORG_ID}/contacts/{VICTIM_CONTACT_ID}"
    assert await _resolve(handler, monkeypatch, contact_id=hostile) is None
    assert [event for event, _ in captured.calls] == ["target.bad_contact_id"]
    assert VICTIM_ORG_ID not in captured.text()
    assert VICTIM_CONTACT_ID not in captured.text()
    assert ".." not in captured.text()
