"""A real box refuses the committed unsubscribe secret.

Empty env vars are ignored, so an unset ENGAGE_UNSUBSCRIBE_SECRET fell back to the `dev-only-`
default — and anyone who read the repo could forge unsubscribe and tracking tokens.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings


def _build(**kwargs: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        internal_service_token=SecretStr("a" * 48),
        pii_encryption_key=SecretStr("b" * 48),
        **kwargs,
    )


@pytest.mark.parametrize("environment", ["production", "staging", "uat"])
def test_the_committed_default_is_refused_on_a_real_box(environment: str) -> None:
    with pytest.raises(ValidationError, match="engage_unsubscribe_secret"):
        _build(environment=environment)


def test_a_real_secret_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _build(environment="production", engage_unsubscribe_secret=SecretStr("u" * 48))
    assert cfg.environment == "production"


def test_the_default_still_works_in_development() -> None:
    assert _build().engage_unsubscribe_secret.get_secret_value().startswith("dev-only-")
