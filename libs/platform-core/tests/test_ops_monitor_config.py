"""`OpsMonitorSettings` itself: the plain-identifier validation on every name this monitor
interpolates into a query or a metric label (m11), and that it can be constructed in a
production-shaped environment without the two secrets it deliberately never receives.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from platform_core.ops_monitor.config import OpsMonitorSettings


class TestSchemaNameValidation:
    def test_a_plain_schema_name_is_accepted(self) -> None:
        settings = OpsMonitorSettings(edge_relay_schemas="account,product")
        assert [s.schema for s in settings.edge_relay_schemas] == ["account", "product"]

    def test_a_schema_name_with_a_dot_is_rejected(self) -> None:
        """Schemas never have one — a dot there is a queue name or a producer override typo,
        not a real schema."""
        with pytest.raises(ValidationError, match="EDGE_RELAY_SCHEMAS"):
            OpsMonitorSettings(edge_relay_schemas="ac.count")

    def test_a_schema_name_with_a_hyphen_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="EDGE_RELAY_SCHEMAS"):
            OpsMonitorSettings(edge_relay_schemas="ac-count")

    def test_a_schema_name_that_could_break_out_of_a_quoted_identifier_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="EDGE_RELAY_SCHEMAS"):
            OpsMonitorSettings(edge_relay_schemas='account"; drop table outbox; --')


class TestDlqAndApiNameValidation:
    def test_a_dot_is_allowed_in_a_dlq_queue_name(self) -> None:
        settings = OpsMonitorSettings(ops_monitor_dlq_queues="account.events,notification.dispatch")
        assert settings.ops_monitor_dlq_queues == ["account.events", "notification.dispatch"]

    def test_a_hyphen_is_allowed_in_an_api_container_name(self) -> None:
        settings = OpsMonitorSettings(ops_monitor_api_containers="app-gateway,auth")
        assert settings.ops_monitor_api_containers == ["app-gateway", "auth"]

    def test_an_invalid_dlq_queue_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="OPS_MONITOR_DLQ_QUEUES"):
            OpsMonitorSettings(ops_monitor_dlq_queues="account events")

    def test_an_invalid_api_container_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="OPS_MONITOR_API_CONTAINERS"):
            OpsMonitorSettings(ops_monitor_api_containers="not a hostname")


class TestProductionSecretGuardIsSkipped:
    """m11: ops-monitor holds neither `internal_service_token` nor `pii_encryption_key` for any
    real purpose, and `compose.prod.yml` never hands it either — `ServiceSettings`' own
    production guard for those two must not stop it from starting."""

    def test_constructs_cleanly_in_production_with_no_secrets_set(self) -> None:
        # debug=False pinned explicitly: this worktree's own dev .env sets DEBUG=true, which
        # `OpsMonitorSettings` would otherwise inherit and correctly refuse in "production" —
        # a different guard than the one this test is about.
        settings = OpsMonitorSettings(environment="production", debug=False)
        assert settings.environment == "production"

    def test_debug_true_in_production_still_raises(self) -> None:
        """Only the secret half of the base guard is skipped (m11) — `debug` is unrelated to
        either secret and ops-monitor keeps the same hygiene every other service has."""
        with pytest.raises(ValueError, match="debug must be off"):
            OpsMonitorSettings(environment="production", debug=True)

    def test_dev_only_secrets_do_not_raise_even_though_the_base_class_would(self) -> None:
        settings = OpsMonitorSettings(
            environment="production",
            debug=False,
            internal_service_token="dev-only-internal-token-padded-to-32-bytes-minimum",
            pii_encryption_key="dev-only-pii-key-change-me",
        )
        assert settings.environment == "production"
