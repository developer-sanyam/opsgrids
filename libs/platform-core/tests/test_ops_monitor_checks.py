"""The checks that need no `make infra` — disk and backup-marker freshness read the local
filesystem only. The ones that need real Postgres/RabbitMQ/HTTP
(`test_ops_monitor_integration.py`) are marked `integration`.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from platform_core.ops_monitor.checks import (
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
    def test_a_gaierror_in_the_cause_chain_is_a_dns_failure(self) -> None:
        assert _is_dns_failure(_dns_failure()) is True

    def test_connection_refused_is_not_a_dns_failure(self) -> None:
        assert _is_dns_failure(_connection_refused()) is False

    def test_a_bare_exception_with_no_cause_is_not_a_dns_failure(self) -> None:
        assert _is_dns_failure(RuntimeError("boom")) is False


class TestApiReadinessSkipsUndeployedProfiles:
    """B3: a container name that does not resolve at all (its product profile is off on this
    box) is skipped — absent from the result — never reported as a failing observation that
    would eventually page someone for a container this box was never going to run."""

    async def test_a_dns_failure_is_skipped_not_failed(self) -> None:
        client = _FakeClient({"employee": _dns_failure()})
        result = await check_api_readiness(client, {"employee": 8000})  # type: ignore[arg-type]
        assert result == {}

    async def test_connection_refused_is_a_real_failure(self) -> None:
        client = _FakeClient({"auth": _connection_refused()})
        result = await check_api_readiness(client, {"auth": 8000})  # type: ignore[arg-type]
        assert result["api.auth"].ok is False

    async def test_a_healthy_container_reports_ok(self) -> None:
        client = _FakeClient({"auth": _FakeResponse(200, {"status": "ok"})})
        result = await check_api_readiness(client, {"auth": 8000})  # type: ignore[arg-type]
        assert result["api.auth"].ok is True

    async def test_one_skipped_and_one_real_container_are_both_handled_in_one_call(self) -> None:
        client = _FakeClient(
            {
                "employee": _dns_failure(),  # profile off — skipped
                "auth": _FakeResponse(200, {"status": "ok"}),  # always-on — reported
            }
        )
        result = await check_api_readiness(client, {"employee": 8000, "auth": 8000})  # type: ignore[arg-type]
        assert "api.employee" not in result
        assert result["api.auth"].ok is True


class TestCoreOnlyDefaults:
    """B3: the default API-container and DLQ-queue lists are the always-on core only — never
    every product this platform could sell, which used to mean a permanent false alarm (and a
    dead daily digest, since `evaluate_digest` never sees "all quiet") on any box that does not
    run every profile."""

    def test_api_containers_default_to_core_only(self) -> None:
        settings = OpsMonitorSettings()
        assert settings.ops_monitor_api_containers == list(CORE_API_CONTAINERS)
        assert len(settings.ops_monitor_api_containers) == 10

    def test_dlq_queues_default_to_core_only(self) -> None:
        settings = OpsMonitorSettings()
        assert settings.ops_monitor_dlq_queues == list(CORE_DLQ_QUEUES)

    def test_an_empty_env_value_also_falls_back_to_core_only(
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
