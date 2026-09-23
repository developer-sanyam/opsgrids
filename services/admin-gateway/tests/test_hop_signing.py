"""The operator gateway is the one gateway that signs the admin hop header (`sign_admin_hop=True`
in `app/main.py`), and refuses to start in a real environment without the key to do it with
(NIT 8 of the final security review) — otherwise every operator request would go out unsigned and
auth would reject each one with nothing in the logs pointing at the missing secret."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.main import policy


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "internal_service_token": SecretStr("x" * 40 + "-internal-token-value"),
        "pii_encryption_key": SecretStr("y" * 40 + "-pii-encryption-key"),
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]


def test_this_gateways_policy_is_the_signing_one() -> None:
    assert policy.sign_admin_hop is True


@pytest.mark.parametrize("environment", ["production", "staging", "uat"])
def test_a_real_environment_without_the_key_refuses_to_start(environment: str) -> None:
    with pytest.raises(ValueError, match="admin_gateway_hop_key must be set"):
        _settings(environment=environment, debug=False)


@pytest.mark.parametrize("environment", ["local", "test", "ci"])
def test_development_without_the_key_starts(environment: str) -> None:
    assert _settings(environment=environment).admin_gateway_hop_key is None


def test_a_real_environment_with_the_key_starts() -> None:
    cfg = _settings(
        environment="production",
        debug=False,
        admin_gateway_hop_key=SecretStr("z" * 40 + "-hop-key-value"),
    )
    assert cfg.admin_gateway_hop_key is not None


class TestTheMetricsRouteIsNarrowedToTheOnePathTheConsoleServes:
    """Merge fix: `tests/infra/test_metrics_never_routed.py` forbids any gateway route *prefix*
    ending in `/metrics` (Prometheus' unauthenticated `/metrics` must never be proxied). The
    operator console's business metrics live at `/api/v1/admin/metrics/overview`, staff-only,
    and that is the only path the SPA calls — so the route is that exact path, not the broader
    `/api/v1/admin/metrics` prefix that tripped the lint."""

    def test_the_overview_path_resolves_to_console_and_is_staff_only(self) -> None:
        from app.main import routes

        route = routes.resolve("/api/v1/admin/metrics/overview")
        assert route is not None
        assert route.upstream.endswith(":8170"), "console"
        assert route.staff_only is True

    def test_the_bare_metrics_prefix_is_not_routed(self) -> None:
        from app.main import routes

        assert routes.resolve("/api/v1/admin/metrics") is None
        assert routes.resolve("/api/v1/admin/metrics/") is None

    def test_no_route_prefix_ends_in_metrics(self) -> None:
        """The property the infra lint enforces, asserted in this service's own suite too."""
        from app.main import routes

        assert [r.prefix for r in routes if r.prefix.rstrip("/").endswith("/metrics")] == []
