"""`OpsMonitorSettings` itself: the plain-identifier validation on every name this monitor
interpolates into a query or a metric label (m11), and that it can be constructed in a
production-shaped environment without the two secrets it deliberately never receives.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from platform_core.ops_monitor.config import (
    CORE_API_CONTAINERS,
    CORE_DLQ_QUEUES,
    OpsMonitorSettings,
    expand_profiles,
    expected_api_containers,
    expected_dlq_queues,
)


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


class TestExpectedNamesFromProfiles:
    """B1 (round 2): what this monitor checks is derived from `COMPOSE_PROFILES`, never from
    what happens to answer a probe — a container this box's profiles say should exist is
    checked whether or not it currently resolves; that is exactly the point (B1's fix)."""

    def test_no_profiles_is_core_only(self) -> None:
        assert expected_api_containers([]) == list(CORE_API_CONTAINERS)
        assert expected_dlq_queues([]) == list(CORE_DLQ_QUEUES)

    def test_hrms_adds_its_own_api_containers_and_queues(self) -> None:
        containers = expected_api_containers(["hrms"])
        assert containers == [*CORE_API_CONTAINERS, "approval", "employee", "attendance", "leave"]
        queues = expected_dlq_queues(["hrms"])
        assert queues == [*CORE_DLQ_QUEUES, "approval.events", "attendance.events", "leave.events"]

    def test_a_profile_with_no_worker_adds_containers_but_no_queues(self) -> None:
        """`crm` runs the `customer`, `deal` and `lead` APIs and one worker (customer's) — the
        lead and deal services have no consumer, so only `customer.events` is added."""
        assert expected_api_containers(["crm"]) == [
            *CORE_API_CONTAINERS,
            "customer",
            "deal",
            "lead",
        ]
        assert expected_dlq_queues(["crm"]) == [*CORE_DLQ_QUEUES, "customer.events"]

    def test_every_profile_together_is_the_full_platform(self) -> None:
        all_profiles = [
            "inventory",
            "storefront",
            "hrms",
            "crm",
            "pulseboard",
            "projects",
            "support",
            "marketing",
            "finance",
            "itsm",
        ]
        containers = expected_api_containers(all_profiles)
        # Every core container, plus every profile's own — nothing dropped, nothing duplicated.
        assert len(containers) == len(set(containers))
        assert set(CORE_API_CONTAINERS) <= set(containers)
        assert "operations" in containers  # inventory
        assert "storefront" in containers and "document" in containers
        assert "finance" in containers and "itsm" in containers

    def test_settings_computes_the_same_list_end_to_end(self) -> None:
        settings = OpsMonitorSettings(compose_profiles="hrms,finance")
        assert settings.ops_monitor_api_containers == expected_api_containers(["hrms", "finance"])
        assert settings.ops_monitor_dlq_queues == expected_dlq_queues(["hrms", "finance"])

    def test_an_explicit_override_replaces_the_computed_list_entirely(self) -> None:
        settings = OpsMonitorSettings(
            compose_profiles="hrms", ops_monitor_api_containers="auth,employee"
        )
        assert settings.ops_monitor_api_containers == ["auth", "employee"]


class TestComposeProfilesValidation:
    def test_a_plain_profile_name_is_accepted(self) -> None:
        settings = OpsMonitorSettings(compose_profiles="hrms,finance")
        assert settings.compose_profiles == ["hrms", "finance"]

    def test_an_invalid_profile_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="COMPOSE_PROFILES"):
            OpsMonitorSettings(compose_profiles="hrms;drop table x")


class TestComposeProfilesWildcard:
    """m10 (round 3): `COMPOSE_PROFILES=*` is docker compose's own "every profile". Before
    this it was either rejected by the identifier check or (past it) looked up as a profile
    literally named `*`, matching nothing — a box running every product monitored only its
    core containers, silently."""

    _ALL: ClassVar[list[str]] = [
        "inventory",
        "storefront",
        "hrms",
        "crm",
        "pulseboard",
        "projects",
        "support",
        "marketing",
        "finance",
        "itsm",
    ]

    def test_the_wildcard_is_accepted_by_the_validator(self) -> None:
        settings = OpsMonitorSettings(compose_profiles="*")
        assert settings.compose_profiles == ["*"]

    def test_the_wildcard_expands_to_every_known_profile(self) -> None:
        assert expand_profiles(["*"]) == sorted(self._ALL)
        assert set(expected_api_containers(["*"])) == set(expected_api_containers(self._ALL))
        assert set(expected_dlq_queues(["*"])) == set(expected_dlq_queues(self._ALL))

    def test_the_wildcard_is_not_silently_core_only(self) -> None:
        settings = OpsMonitorSettings(compose_profiles="*")
        assert "operations" in settings.ops_monitor_api_containers  # inventory
        assert "approval.events" in settings.ops_monitor_dlq_queues  # hrms worker
        assert "finance.events" in settings.ops_monitor_dlq_queues  # finance worker

    @pytest.mark.parametrize("raw", ["hrms,*", "*,hrms", "crm, * ,finance"])
    def test_the_wildcard_anywhere_in_the_list_means_every_profile(self, raw: str) -> None:
        """Post-review fixup: docker compose treats `*` as "every profile" wherever it sits in
        `COMPOSE_PROFILES` (`hrms,*` runs everything — rendered in `tests/infra/
        test_ops_monitor_profile_coupling.py`), so a monitor that only understood the sole-entry
        form would watch `hrms` on a box compose is running whole."""
        settings = OpsMonitorSettings(compose_profiles=raw)
        assert expand_profiles(settings.compose_profiles) == sorted(self._ALL)
        assert set(settings.ops_monitor_api_containers) == set(expected_api_containers(self._ALL))
        assert set(settings.ops_monitor_dlq_queues) == set(expected_dlq_queues(self._ALL))

    def test_a_bad_name_beside_the_wildcard_is_still_rejected(self) -> None:
        with pytest.raises(ValidationError, match="COMPOSE_PROFILES"):
            OpsMonitorSettings(compose_profiles="hrms;drop table x,*")

    def test_no_profile_and_a_named_profile_are_untouched_by_expansion(self) -> None:
        assert expand_profiles([]) == []
        assert expand_profiles(["hrms"]) == ["hrms"]


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
