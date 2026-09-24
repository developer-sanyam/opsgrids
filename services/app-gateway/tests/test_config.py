"""MINOR 2 of the 2026-09-24 SSO review: an empty `auth_refresh_cors_origins` on app-gateway in
a real deployment does not fail loudly — every product SPA's cross-origin `/auth/refresh` and
`/auth/logout` call is silently refused by CORS at the browser, which the client only ever sees
as `refreshAccessToken()`'s generic `"error"` outcome. This pins the fail-fast instead.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import Settings


class TestAuthRefreshCorsOriginsIsRequiredInARealDeployment:
    def _settings(self, **overrides: object) -> Settings:
        base: dict[str, object] = {
            "internal_service_token": SecretStr("x" * 40 + "-internal-token-value"),
            "pii_encryption_key": SecretStr("y" * 40 + "-pii-encryption-key"),
        }
        return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]

    @pytest.mark.parametrize("environment", ["production", "staging", "uat"])
    def test_a_real_deployment_with_none_configured_refuses_to_start(
        self, environment: str
    ) -> None:
        with pytest.raises(ValueError, match="auth_refresh_cors_origins must be set"):
            self._settings(environment=environment, debug=False)

    @pytest.mark.parametrize("environment", ["production", "staging", "uat"])
    def test_a_real_deployment_with_one_configured_starts(self, environment: str) -> None:
        cfg = self._settings(
            environment=environment,
            debug=False,
            auth_refresh_cors_origins="https://people.opsgrids.com",
        )
        assert cfg.auth_refresh_cors_origins == ["https://people.opsgrids.com"]

    @pytest.mark.parametrize("environment", ["local", "test", "ci"])
    def test_development_starts_with_none_configured(self, environment: str) -> None:
        cfg = self._settings(environment=environment)
        assert cfg.auth_refresh_cors_origins == []
