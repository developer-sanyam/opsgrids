"""Every service's own `/metrics` (`app.py`'s health router) — db pool occupancy always,
outbox pending/oldest/exhausted only when the service declares an outbox model, the same gate
`/health/outbox` uses and for the same reason (an app-role connection binds no tenant and would
report zero rows regardless of the real backlog).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, DateTime, Integer, MetaData, Table

from platform_core.app import create_app
from platform_core.config import ServiceSettings

_OUTBOX_TABLE = Table(
    "outbox",
    MetaData(),
    Column("published_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("attempts", Integer),
)


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


class _FakePool:
    def __init__(self, in_use: int) -> None:
        self._in_use = in_use

    def checkedout(self) -> int:
        return self._in_use


class _FakeEngine:
    def __init__(self, in_use: int = 0) -> None:
        self.pool = _FakePool(in_use)


class _FakeResult:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def one(self) -> tuple[Any, ...]:
        return self._row


class _FakeConnection:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, *args: Any, **kwargs: Any) -> _FakeResult:
        return _FakeResult(self._row)


class _FakeOutboxEngine:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self._row)


async def _get(app: FastAPI, path: str) -> str:
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get(path)
    assert response.status_code == 200
    return response.text


def _app(**kwargs: Any) -> FastAPI:
    return create_app(_Settings(), title="probe", **kwargs)


class TestMetricsRoute:
    async def test_without_a_database_reports_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = await _get(_app(database=False), "/metrics")
        assert text == ""

    async def test_a_database_reports_pool_occupancy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        monkeypatch.setattr("platform_core.app.assert_app_role", _noop)
        monkeypatch.setattr("platform_core.app.get_engine", lambda: _FakeEngine(in_use=3))

        text = await _get(_app(), "/metrics")
        assert "db_pool_in_use 3.0" in text
        assert "outbox_pending" not in text

    async def test_an_outbox_model_reports_the_three_gauges(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        monkeypatch.setattr("platform_core.app.assert_app_role", _noop)
        monkeypatch.setattr("platform_core.app.init_outbox_engine", lambda s: None)
        monkeypatch.setattr("platform_core.app.get_engine", lambda: _FakeEngine(in_use=0))
        monkeypatch.setattr(
            "platform_core.app.get_outbox_engine", lambda: _FakeOutboxEngine((7, 42.5, 1))
        )

        outbox_model = type("Outbox", (), {"__table__": _OUTBOX_TABLE})
        text = await _get(_app(outbox_model=outbox_model), "/metrics")
        assert "outbox_pending 7.0" in text
        assert "outbox_oldest_seconds 42.5" in text
        assert "outbox_exhausted 1.0" in text

    async def test_help_and_type_comments_are_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        monkeypatch.setattr("platform_core.app.assert_app_role", _noop)
        monkeypatch.setattr("platform_core.app.get_engine", lambda: _FakeEngine(in_use=0))

        text = await _get(_app(), "/metrics")
        assert "# TYPE db_pool_in_use gauge" in text


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None
