"""`app/account_client.py`'s pagination and retry (ADR-0020 §3: "3 retries at 2/8/30 s")."""

from __future__ import annotations

import httpx
import pytest

from app import account_client
from app.config import settings as settings_factory


def _row(organization_id: str, updated_at: str) -> dict[str, object]:
    return {
        "organization_id": organization_id,
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "fiscal_year_start_month": 4,
        "status": "active",
        "updated_at": updated_at,
    }


async def test_fetch_page_parses_rows_and_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/organizations/export"
        assert request.headers["X-Internal-Token"]
        return httpx.Response(
            200,
            json={
                "rows": [_row("11111111-1111-1111-1111-111111111111", "2026-09-01T00:00:00Z")],
                "next": {"after_updated_at": "2026-09-01T00:00:00Z", "after_id": "1111"},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rows, cursor = await account_client.fetch_page(
        settings_factory(), client=client, after_updated_at=None, after_id=None
    )
    assert len(rows) == 1
    assert rows[0].organization_id == "11111111-1111-1111-1111-111111111111"
    assert cursor == {"after_updated_at": "2026-09-01T00:00:00Z", "after_id": "1111"}


async def test_fetch_all_follows_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {
            "rows": [_row("11111111-1111-1111-1111-111111111111", "2026-09-01T00:00:00Z")],
            "next": {"after_updated_at": "2026-09-01T00:00:00Z", "after_id": "1"},
        },
        {
            "rows": [_row("22222222-2222-2222-2222-222222222222", "2026-09-02T00:00:00Z")],
            "next": None,
        },
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    # `fetch_all` opens its own client via `_client`; monkeypatch it to use the mock transport.
    monkeypatch.setattr(
        account_client,
        "_client",
        lambda _settings: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    rows = await account_client.fetch_all(settings_factory())

    assert calls["n"] == 2
    assert [r.organization_id for r in rows] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]


async def test_fetch_page_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account_client, "RETRY_BACKOFFS_SECONDS", (0.0, 0.0, 0.0))
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("simulated network blip")
        return httpx.Response(200, json={"rows": [], "next": None})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rows, cursor = await account_client.fetch_page(
        settings_factory(), client=client, after_updated_at=None, after_id=None
    )
    assert rows == []
    assert cursor is None
    assert attempts["n"] == 3


async def test_fetch_page_raises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account_client, "RETRY_BACKOFFS_SECONDS", (0.0, 0.0, 0.0))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("account is down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(account_client.AccountExportError):
        await account_client.fetch_page(
            settings_factory(), client=client, after_updated_at=None, after_id=None
        )
