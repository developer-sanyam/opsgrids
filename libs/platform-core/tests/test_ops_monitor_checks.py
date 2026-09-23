"""The checks that need no `make infra` — disk and backup-marker freshness read the local
filesystem only. The ones that need real Postgres/RabbitMQ/HTTP
(`test_ops_monitor_integration.py`) are marked `integration`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from platform_core.ops_monitor.checks import check_backup_freshness, check_disk
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
