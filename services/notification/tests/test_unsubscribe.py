"""The unsubscribe token — signed, unguessable, tested without a database.

`sign`/`verify` are pure functions of their arguments and a configured secret, so the trust
boundary of Engage's only public write is checked here the same way `test_domain.py` checks the
suppression rule: without Postgres, because nothing here touches it.
"""

from __future__ import annotations

import uuid

import pytest

from app import unsubscribe
from app.config import Settings


def _settings(secret: str = "test-only-unsubscribe-secret-not-real") -> Settings:  # noqa: S107
    # pragma: allowlist secret
    return Settings(engage_unsubscribe_secret=secret)  # type: ignore[arg-type]


def test_a_genuine_token_verifies_to_the_org_and_contact_it_names() -> None:
    org_id, contact_id = uuid.uuid4(), uuid.uuid4()
    settings = _settings()
    token = unsubscribe.sign(org_id, contact_id, settings=settings)

    target = unsubscribe.verify(token, settings=settings)

    assert target.organization_id == org_id
    assert target.contact_id == contact_id


def test_two_signings_of_the_same_pair_are_stable() -> None:
    """Unlike `pii.seal`, this is not trying to hide equality — the token is a lookup key handed
    to a stranger, not ciphertext at rest, so a deterministic signature is fine and is in fact
    what lets a mail provider's link-scanning retry the same URL without minting a fresh one."""
    org_id, contact_id = uuid.uuid4(), uuid.uuid4()
    settings = _settings()
    assert unsubscribe.sign(org_id, contact_id, settings=settings) == unsubscribe.sign(
        org_id, contact_id, settings=settings
    )


def test_a_tampered_payload_is_refused() -> None:
    settings = _settings()
    token = unsubscribe.sign(uuid.uuid4(), uuid.uuid4(), settings=settings)
    body, sig = token.split(".", 1)
    tampered = f"{body}x.{sig}"

    with pytest.raises(unsubscribe.UnsubscribeTokenError):
        unsubscribe.verify(tampered, settings=settings)


def test_a_token_signed_under_a_different_secret_is_refused() -> None:
    org_id, contact_id = uuid.uuid4(), uuid.uuid4()
    token = unsubscribe.sign(  # pragma: allowlist secret
        org_id, contact_id, settings=_settings("secret-a-is-32-bytes-long-ok")
    )

    with pytest.raises(unsubscribe.UnsubscribeTokenError):
        unsubscribe.verify(  # pragma: allowlist secret
            token, settings=_settings("secret-b-is-also-32-bytes-long")
        )


@pytest.mark.parametrize("garbage", ["", "no-dot-here", "not base64!!.also-not-base64!!"])
def test_malformed_tokens_are_refused_rather_than_raising_an_unhandled_error(garbage: str) -> None:
    with pytest.raises(unsubscribe.UnsubscribeTokenError):
        unsubscribe.verify(garbage, settings=_settings())
