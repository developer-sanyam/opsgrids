"""The checks that need no `make infra` — disk and backup-marker freshness read the local
filesystem only. The ones that need real Postgres/RabbitMQ/HTTP
(`test_ops_monitor_integration.py`) are marked `integration`.
"""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from platform_core.edge_relay import SchemaSpec
from platform_core.ops_monitor.checks import (
    _check_one_dlq_queue,
    _check_one_outbox_schema,
    _is_dns_failure,
    check_api_readiness,
    check_backup_freshness,
    check_disk,
)
from platform_core.ops_monitor.config import (
    CORE_API_CONTAINERS,
    CORE_DLQ_QUEUES,
    OpsMonitorSettings,
)
from platform_core.ops_monitor.main import parse_api_containers


class TestDisk:
    def test_reports_free_percentage_of_the_given_path(self, tmp_path: Path) -> None:
        result = check_disk(str(tmp_path), min_free_percent=0.0)
        assert result["disk"].ok is True
        assert "% free" in result["disk"].detail

    def test_fails_when_free_percent_is_below_the_threshold(self, tmp_path: Path) -> None:
        # No real disk is ever 100% free, so this threshold cannot be met.
        result = check_disk(str(tmp_path), min_free_percent=100.0)
        assert result["disk"].ok is False


class TestBackupFreshness:
    def test_no_marker_is_ok_but_distinctly_labelled(self, tmp_path: Path) -> None:
        result = check_backup_freshness(tmp_path / "backup.last-ok", max_age_hours=26)
        assert result["backup"].ok is True
        assert "no marker yet" in result["backup"].detail

    def test_a_fresh_marker_is_ok(self, tmp_path: Path) -> None:
        marker = tmp_path / "backup.last-ok"
        now = datetime(2026, 1, 2, tzinfo=UTC)
        marker.write_text((now - timedelta(hours=1)).isoformat(), encoding="utf-8")
        result = check_backup_freshness(marker, max_age_hours=26, now=now)
        assert result["backup"].ok is True

    def test_a_stale_marker_fails(self, tmp_path: Path) -> None:
        marker = tmp_path / "backup.last-ok"
        now = datetime(2026, 1, 2, tzinfo=UTC)
        marker.write_text((now - timedelta(hours=27)).isoformat(), encoding="utf-8")
        result = check_backup_freshness(marker, max_age_hours=26, now=now)
        assert result["backup"].ok is False
        assert "27.0h ago" in result["backup"].detail

    def test_an_unreadable_marker_fails_rather_than_crashing(self, tmp_path: Path) -> None:
        marker = tmp_path / "backup.last-ok"
        marker.write_text("not-a-timestamp", encoding="utf-8")
        result = check_backup_freshness(marker, max_age_hours=26)
        assert result["backup"].ok is False
        assert "unreadable marker" in result["backup"].detail


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = {"content-type": "application/json"} if body is not None else {}

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    """Stands in for `httpx.AsyncClient` — `.get(url)` either raises what the test scripts or
    returns a canned response, keyed by container name embedded in the URL."""

    def __init__(self, by_host: dict[str, Any]) -> None:
        self._by_host = by_host

    async def get(self, url: str, timeout: float = 5.0) -> _FakeResponse:  # noqa: ASYNC109 — matching httpx.AsyncClient.get's real signature, not a real timeout
        host = url.split("//", 1)[1].split(":", 1)[0]
        outcome = self._by_host[host]
        if isinstance(outcome, BaseException):
            raise outcome
        response: _FakeResponse = outcome
        return response


def _dns_failure() -> httpx.ConnectError:
    """A `ConnectError` whose cause chain bottoms out in `socket.gaierror` — exactly the shape
    httpx raises for a hostname the compose network's embedded DNS cannot resolve at all."""
    gaierror = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    try:
        raise httpx.ConnectError("[Errno -2] Name or service not known") from gaierror
    except httpx.ConnectError as exc:
        return exc


def _connection_refused() -> httpx.ConnectError:
    """A `ConnectError` for a name that *does* resolve but nothing is listening — a real
    failure, never mistaken for "this profile is off"."""
    refused = ConnectionRefusedError("Connection refused")
    try:
        raise httpx.ConnectError("[Errno 61] Connection refused") from refused
    except httpx.ConnectError as exc:
        return exc


class TestIsDnsFailure:
    """`_is_dns_failure` only phrases the failure message now (round 2) — it is never a reason
    to treat the check as passing. See `TestApiReadinessNeverSkips` below for that half."""

    def test_a_gaierror_in_the_cause_chain_is_a_dns_failure(self) -> None:
        assert _is_dns_failure(_dns_failure()) is True

    def test_connection_refused_is_not_a_dns_failure(self) -> None:
        assert _is_dns_failure(_connection_refused()) is False

    def test_a_bare_exception_with_no_cause_is_not_a_dns_failure(self) -> None:
        assert _is_dns_failure(RuntimeError("boom")) is False


class TestApiReadinessNeverSkips:
    """B1 (round 2): a container name that does not resolve at all is a REAL failure, never
    skipped. Docker's embedded DNS drops a stopped, crashed, OOM-killed or restarting
    container exactly the way it drops one whose profile was never turned on — there is no way
    to tell those apart from a DNS failure alone, so treating it as "not deployed" would hide a
    crashed container behind the exact same symptom a fine, undeployed one produces. Every
    container this monitor is told to check is one `COMPOSE_PROFILES` says should exist
    (`config.expected_api_containers`); a config change is how a box says a container should no
    longer be watched, not a DNS failure at check time."""

    async def test_a_dns_failure_is_a_real_failure(self) -> None:
        client = _FakeClient({"employee": _dns_failure()})
        result = await check_api_readiness(client, {"employee": 8000})  # type: ignore[arg-type]
        assert result["api.employee"].ok is False
        assert "does not resolve" in result["api.employee"].detail

    async def test_connection_refused_is_also_a_real_failure(self) -> None:
        client = _FakeClient({"auth": _connection_refused()})
        result = await check_api_readiness(client, {"auth": 8000})  # type: ignore[arg-type]
        assert result["api.auth"].ok is False

    async def test_a_healthy_container_reports_ok(self) -> None:
        client = _FakeClient({"auth": _FakeResponse(200, {"status": "ok"})})
        result = await check_api_readiness(client, {"auth": 8000})  # type: ignore[arg-type]
        assert result["api.auth"].ok is True

    async def test_a_failing_and_a_healthy_container_are_both_reported_in_one_call(self) -> None:
        client = _FakeClient(
            {
                "employee": _dns_failure(),
                "auth": _FakeResponse(200, {"status": "ok"}),
            }
        )
        result = await check_api_readiness(client, {"employee": 8000, "auth": 8000})  # type: ignore[arg-type]
        assert result["api.employee"].ok is False
        assert result["api.auth"].ok is True


class TestApiReadinessConcurrency:
    """m5: every container is probed concurrently, each with its own timeout — five slow
    containers must finish in about one timeout period, not five."""

    async def test_five_slow_containers_finish_concurrently_not_sequentially(self) -> None:
        class _SlowClient:
            async def get(self, url: str, timeout: float = 5.0) -> _FakeResponse:  # noqa: ASYNC109
                await asyncio.sleep(10)
                return _FakeResponse(200, {"status": "ok"})

        containers = {f"svc{i}": 8000 for i in range(5)}
        start = asyncio.get_event_loop().time()
        result = await check_api_readiness(_SlowClient(), containers, timeout_seconds=0.1)  # type: ignore[arg-type]
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 1.0, f"took {elapsed:.2f}s — probes ran sequentially, not concurrently"
        assert len(result) == 5
        for i in range(5):
            assert result[f"api.svc{i}"].ok is False
            assert "svc" in result[f"api.svc{i}"].detail
            assert "timed out" in result[f"api.svc{i}"].detail


class TestApiContainersDefaultsFromProfiles:
    """B1/m6 (round 2): the default API-container and DLQ-queue lists derive from
    `COMPOSE_PROFILES` — core when no profile is enabled, core plus each profile's own
    containers/queues when profiles are — never a fixed guess that misses everything beyond
    core on a box selling more than the base platform. Full coverage of the derivation itself
    lives in `test_ops_monitor_config.py`; this is just the "no profiles" boundary case,
    exercised the same way B3 originally was."""

    def test_no_profiles_means_core_only(self) -> None:
        settings = OpsMonitorSettings()
        assert settings.ops_monitor_api_containers == list(CORE_API_CONTAINERS)
        assert settings.ops_monitor_dlq_queues == list(CORE_DLQ_QUEUES)

    def test_an_empty_env_value_also_falls_back_to_the_computed_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`compose.prod.yml` passes `${OPS_MONITOR_API_CONTAINERS:-}` — empty when the
        operator has not set it — and `ServiceSettings`' `env_ignore_empty` must treat that
        the same as unset, not as an explicit empty list. This has to go through a real
        environment variable, not a constructor keyword: `env_ignore_empty` is a
        pydantic-settings *source* behaviour and does not apply to values passed directly."""
        monkeypatch.setenv("OPS_MONITOR_API_CONTAINERS", "")
        monkeypatch.setenv("OPS_MONITOR_DLQ_QUEUES", "")
        settings = OpsMonitorSettings()
        assert settings.ops_monitor_api_containers == list(CORE_API_CONTAINERS)
        assert settings.ops_monitor_dlq_queues == list(CORE_DLQ_QUEUES)


class TestParseApiContainers:
    def test_bare_name_defaults_to_port_8000(self) -> None:
        assert parse_api_containers(["auth"]) == {"auth": 8000}

    def test_name_with_explicit_port(self) -> None:
        assert parse_api_containers(["auth:8001"]) == {"auth": 8001}

    def test_several_entries(self) -> None:
        assert parse_api_containers(["auth", "account:8110"]) == {
            "auth": 8000,
            "account": 8110,
        }


class TestOutboxConnectTimeout:
    """m9 (round 3): before this, only the query itself (`SET LOCAL statement_timeout`) was
    bounded — acquiring the connection in the first place (pool checkout, then the TCP
    handshake) was not wrapped in anything of this function's own. A pool exhausted by other
    concurrent schema checks, or a host that accepts the socket and never answers, hung here
    with nothing bounding it but `main._collect`'s own *outer* timeout — which discards every
    schema's result for this cycle, not just the slow one's."""

    async def test_a_hanging_connect_times_out_as_its_own_observation(self) -> None:
        class _HangingConnectEngine:
            def connect(self) -> _HangingConnectEngine:
                return self

            async def __aenter__(self) -> _HangingConnectEngine:
                await asyncio.sleep(10)
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

        name, obs = await _check_one_outbox_schema(
            _HangingConnectEngine(),  # type: ignore[arg-type]
            SchemaSpec(schema="account", producer="account"),
            warn_seconds=60,
            crit_seconds=300,
            max_attempts=5,
            statement_timeout_seconds=0.05,
        )
        assert name == "outbox.account"
        assert obs is not None
        assert obs.ok is False
        assert "timed out" in obs.detail

    async def test_a_fast_connect_is_unaffected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The wrapping wait_for must not itself become the thing that fails a perfectly
        healthy schema — a regression this fast/slow pair together guards against."""

        class _Result:
            def __init__(self, value: object) -> None:
                self._value = value

            def scalar(self) -> object:
                return self._value

            def one(self) -> tuple[int, float, int]:
                return (0, 0.0, 0)

        class _FastConn:
            async def execute(self, statement: object, params: object | None = None) -> _Result:
                text_str = str(statement)
                if "to_regclass" in text_str:
                    return _Result("account.outbox")
                return _Result(None)

        class _FastEngine:
            def connect(self) -> _FastEngine:
                return self

            async def __aenter__(self) -> _FastConn:
                return _FastConn()

            async def __aexit__(self, *exc: object) -> None:
                return None

        name, obs = await _check_one_outbox_schema(
            _FastEngine(),  # type: ignore[arg-type]
            SchemaSpec(schema="account", producer="account"),
            warn_seconds=60,
            crit_seconds=300,
            max_attempts=5,
            statement_timeout_seconds=5.0,
        )
        assert name == "outbox.account"
        assert obs is not None
        assert obs.ok is True


class TestDlqChannelOpenTimeout:
    """m9 (round 3): opening the channel itself used to happen before the function's own
    `try`, outside any timeout — a broker that accepts the connection but never answers
    `Channel.Open` hung here with nothing bounding it but the outer `_collect` timeout, which
    would then discard every queue's result in the same `check_dlq_depth` call, not just this
    one's."""

    async def test_a_hanging_channel_open_times_out_as_its_own_observation(self) -> None:
        class _HangingConnection:
            async def channel(self) -> object:
                await asyncio.sleep(10)
                raise AssertionError("unreachable")  # pragma: no cover

        name, obs = await _check_one_dlq_queue(
            _HangingConnection(),  # type: ignore[arg-type]
            "notification.dispatch",
            timeout_seconds=0.05,
        )
        assert name == "dlq.notification.dispatch"
        assert obs.ok is False
        assert "timed out" in obs.detail
