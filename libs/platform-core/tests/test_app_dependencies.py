"""A service gets exactly the dependencies it declares — and readiness reports them.

This exists because of a real defect. account-service bumps the organization's permission
version on every write that changes what somebody may do, and that bump calls `get_redis()`,
which **raises** when Redis was never initialised. account-service never initialised it. Every
role change, member revocation and location move would have raised `RuntimeError` in
production.

Its 63 tests passed throughout, because the fixture injected a fake client straight into the
cache module and never exercised the lifespan that was missing the call.

The fix made the dependency a parameter of `create_app`, so forgetting it is a visible omission
at the call site rather than a crash in production. These tests guard that mechanism.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from platform_core.app import create_app
from platform_core.config import ServiceSettings


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


class _FakeRedis:
    def __init__(self, *, alive: bool = True) -> None:
        self.alive = alive
        self.pinged = False

    async def ping(self) -> bool:
        self.pinged = True
        if not self.alive:
            raise ConnectionError("redis is down")
        return True

    async def aclose(self) -> None:
        return None


def _app(**kwargs: Any) -> FastAPI:
    return create_app(_Settings(), title="probe", **kwargs)


async def _ready(app: FastAPI) -> dict[str, Any]:
    """Run the real lifespan, then ask for readiness.

    `ASGITransport` does **not** run lifespan events. Without entering the context
    explicitly, `init_engine` and `init_redis` never fire and the assertions below pass
    whatever the code does — which is precisely the vacuous-green failure this whole module
    exists to prevent.

    `http` is kept separate from the body, which has its own `status` field.
    """
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/health/ready")
    return {"http": response.status_code, "body": response.json()}


class TestDeclaredDependencies:
    async def test_a_service_without_a_database_is_not_asked_about_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gateways own no tables.

        Reporting on a dependency the service does not use is worse than not reporting it:
        it eventually fails for a reason nobody can act on.
        """

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("init_engine must not be called when database=False")

        monkeypatch.setattr("platform_core.app.init_engine", _boom)

        result = await _ready(_app(database=False))
        assert result["body"]["status"] == "ok"
        assert "database" not in result["body"]["checks"]

    async def test_declaring_redis_opens_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The defect in one line: the service used Redis and nothing ever opened it."""
        opened: list[str] = []
        fake = _FakeRedis()

        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        monkeypatch.setattr("platform_core.app.init_redis", lambda url: opened.append(url))
        monkeypatch.setattr("platform_core.app.get_redis", lambda: fake)
        monkeypatch.setattr("platform_core.app.close_redis", _noop)

        result = await _ready(_app(database=False, redis=True))

        assert opened, "redis=True must open a client during the lifespan"
        assert result["body"]["checks"]["redis"] == "ok"
        assert fake.pinged, "readiness must actually reach Redis, not assume it"

    async def test_redis_is_not_opened_unless_declared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("init_redis must not be called when redis=False")

        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        # The role guard needs a real engine and this test has a stub. Its own coverage is
        # in test_tenancy.py, against the database.
        monkeypatch.setattr("platform_core.app.assert_app_role", _noop)
        monkeypatch.setattr("platform_core.app.init_redis", _boom)

        result = await _ready(_app())
        assert "redis" not in result["body"]["checks"]

    async def test_declaring_an_outbox_opens_the_outbox_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The probe counts through `opsgrids_outbox`, so the lifespan has to open it.

        Through the application engine `/health/outbox` reports zero whatever the table
        holds — the app role is NOBYPASSRLS and the probe binds no tenant — which wires the
        alarm to the same blind spot as the stall it exists to expose.
        """
        opened: list[str] = []

        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        monkeypatch.setattr("platform_core.app.assert_app_role", _noop)
        monkeypatch.setattr(
            "platform_core.app.init_outbox_engine", lambda s: opened.append(s.service_name)
        )

        await _ready(_app(outbox_model=type("Outbox", (), {})))
        assert opened == ["probe"], "outbox_model must open the outbox engine during the lifespan"

    async def test_the_outbox_engine_is_not_opened_unless_declared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("init_outbox_engine must not be called without outbox_model")

        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        monkeypatch.setattr("platform_core.app.assert_app_role", _noop)
        monkeypatch.setattr("platform_core.app.init_outbox_engine", _boom)

        await _ready(_app())


class TestReadinessReflectsReality:
    async def test_a_dead_redis_makes_the_service_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point.

        Before this, a service whose Redis was gone reported itself ready and then raised on
        every permission-changing write. Readiness has to go degraded while the dependency is
        down, or it is telling the load balancer something untrue.
        """
        fake = _FakeRedis(alive=False)
        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        monkeypatch.setattr("platform_core.app.init_redis", lambda url: None)
        monkeypatch.setattr("platform_core.app.get_redis", lambda: fake)
        monkeypatch.setattr("platform_core.app.close_redis", _noop)

        result = await _ready(_app(database=False, redis=True))

        assert result["body"]["status"] == "degraded"
        assert result["body"]["checks"]["redis"].startswith("error:")

    async def test_liveness_does_not_depend_on_anything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Restart on `/health`, route on `/health/ready`.

        Conflating them restarts a healthy process that is merely waiting on a dependency.
        """
        monkeypatch.setattr("platform_core.app.init_engine", lambda *a, **k: None)
        app = _app(database=False)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None
