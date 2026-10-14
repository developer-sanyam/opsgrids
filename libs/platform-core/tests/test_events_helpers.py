"""`platform_core.events.actor_for` and `parse_envelope_dt` — the two small helpers every
consumer/producer service used to carry its own copy of."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.events import actor_for, parse_envelope_dt


def _ctx(membership_id: uuid.UUID | None) -> ScopeContext:
    return ScopeContext(
        principal=Principal(
            sub=uuid.UUID(int=1),
            organization_id=uuid.UUID(int=2),
            membership_id=membership_id,
        ),
        permission="x.y.z",
        scope=Scope.ORG,
    )


class TestActorFor:
    def test_names_the_user_and_the_membership(self) -> None:
        membership = uuid.uuid4()
        assert actor_for(_ctx(membership)) == {
            "type": "user",
            "id": str(uuid.UUID(int=1)),
            "membership_id": str(membership),
        }

    def test_a_principal_without_a_membership_emits_null_not_the_string_none(self) -> None:
        assert actor_for(_ctx(None))["membership_id"] is None


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
