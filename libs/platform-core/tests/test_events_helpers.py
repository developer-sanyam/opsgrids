"""`platform_core.events.parse_envelope_dt` — the tolerant timestamp read every consumer service
used to carry its own copy of."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from platform_core.events import parse_envelope_dt


class TestParseEnvelopeDt:
    def test_a_valid_timestamp_is_parsed_and_nothing_is_logged(self) -> None:
        with capture_logs() as logs:
            parsed = parse_envelope_dt("2026-01-05T10:00:00+00:00", "x.malformed")
        assert parsed == datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        assert logs == []

    @pytest.mark.parametrize("raw", [None, "", 0])
    def test_absent_is_now_and_silent(self, raw: Any) -> None:
        with capture_logs() as logs:
            parsed = parse_envelope_dt(raw, "x.malformed")
        assert abs(datetime.now(UTC) - parsed) < timedelta(seconds=5)
        assert logs == []

    @pytest.mark.parametrize("raw", ["yesterday-ish", 12345, ["2026-01-05"]])
    def test_malformed_is_now_and_logs_the_callers_event_name(self, raw: Any) -> None:
        with capture_logs() as logs:
            parsed = parse_envelope_dt(raw, "directory.event.malformed_occurred_at")
        assert abs(datetime.now(UTC) - parsed) < timedelta(seconds=5)
        assert len(logs) == 1
        assert logs[0]["event"] == "directory.event.malformed_occurred_at"
        assert logs[0]["log_level"] == "warning"
        assert logs[0]["raw"] == str(raw)[:100]

    def test_the_logged_value_is_truncated_to_100_characters(self) -> None:
        with capture_logs() as logs:
            parse_envelope_dt("z" * 500, "x.malformed")
        assert len(logs[0]["raw"]) == 100
