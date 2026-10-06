"""The startup refusal of placeholder secrets, exercised the way an operator would hit it.

The contract (documented in the Makefile's secret-scan exclusions) is that `dev-only-` and
`ci-only-` prefixed values are placeholders, and a real deployment refuses to boot on them.
The original guard checked the dev placeholders only — the committed `ci-only-pii-key` from
ci.yml sailed into a hypothetical production boot and every tenant's PII would have been
sealed under a string in the repository. These tests pin the closed hole.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from platform_core.config import ServiceSettings

#: Long enough for every length floor; obviously not a placeholder.
STRONG = "0" * 48


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"

    # Hermetic: `ServiceSettings` reads the repository `.env` so a developer's local values
    # reach every service, which is right in a process and wrong in a test. `.env.example`
    # ships `DEBUG=true`, so a developer who followed the documented setup saw this file fail
    # on a production guard their change never touched. The probe reads only what it is given.
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
        **kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Only the constructor arguments. Nothing from the process environment.

        `env_file=None` above closed half the hole: the `.env` *file* is no longer read. The
        other half is that the Makefile `export`s the names that file defines, so `DEBUG=true`
        arrives as a real environment variable and pydantic reads it anyway — and this file
        failed again, on the same guard, for the same developer following the same documented
        setup. Dropping every source but `init_settings` is what actually makes the probe read
        only what it is given.
        """
        return (init_settings,)


def _production(**overrides: str) -> ServiceSettings:
    values: dict[str, str] = {
        "environment": "production",
        "internal_service_token": STRONG,
        "pii_encryption_key": STRONG,
    }
    values.update(overrides)
    return _Settings(**values)  # type: ignore[arg-type]


class TestPlaceholderRefusal:
    def test_a_fully_configured_production_boots(self) -> None:
        assert _production().is_production

    @pytest.mark.parametrize("environment", ["production", "staging", "uat"])
    def test_the_dev_pii_key_is_refused(self, environment: str) -> None:
        with pytest.raises(ValueError, match="pii_encryption_key"):
            _production(environment=environment, pii_encryption_key="dev-only-pii-key-change-me")

    def test_the_ci_pii_key_is_refused(self) -> None:
        """The committed ci.yml value, verbatim — the one that used to pass."""
        with pytest.raises(ValueError, match="pii_encryption_key"):
            _production(pii_encryption_key="ci-only-pii-key" + "-padded-to-32-bytes-xx")

    def test_an_edited_placeholder_is_still_a_placeholder(self) -> None:
        """Prefix-matched, not exact-matched: renaming the tail must not launder the value."""
        with pytest.raises(ValueError, match="pii_encryption_key"):
            _production(pii_encryption_key="dev-only-pii-key-but-i-changed-it-a-lot-x")

    def test_the_ci_internal_token_is_refused_even_when_long_enough(self) -> None:
        with pytest.raises(ValueError, match="internal_service_token"):
            _production(internal_service_token="ci-only-internal-token-padded-to-32-bytes")

    def test_a_short_pii_key_is_refused_whatever_it_says(self) -> None:
        """The key seals every tenant's PII; a 15-byte passphrase is theatre."""
        with pytest.raises(ValueError, match="at least 32 bytes"):
            _production(pii_encryption_key="short-but-real")

    def test_a_short_internal_token_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 32 bytes"):
            _production(internal_service_token="short-but-real")

    def test_local_development_is_left_alone(self) -> None:
        """The defaults exist so `make run` works; the guard is for real deployments."""
        cfg = _Settings(environment="local")
        assert not cfg.is_production


class TestARefusalNeverPrintsWhatItWasGiven:
    """A refusal is a crash at start-up, printed to stdout and pasted into tickets. pydantic puts
    the whole settings input into a `ValidationError` unless told not to, and the environment
    hands it **plain strings**, not `SecretStr`s — so the tail of whatever secret happened to be
    the last field set would be in that crash. The message names the variable, never a value."""

    def test_the_input_is_not_in_the_error(self) -> None:
        marker = "LEAKMARKER0123456789abcdef"  # pragma: allowlist secret
        with pytest.raises(ValueError, match="pii_encryption_key") as raised:
            # The broker URL, password and all, is the last value given: the tail pydantic used to
            # print.
            _production(
                pii_encryption_key="short", rabbitmq_url=f"amqp://opsgrids:{marker}@broker:5672/"
            )
        text = str(raised.value)
        assert marker not in text
        assert "input_value" not in text
