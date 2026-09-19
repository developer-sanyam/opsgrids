"""Canonicalising a phone number for storage — pure, no database.

Kept separate from `test_contact_phone_pii.py` so these run everywhere: the rule that a number
without a country code is refused rather than guessed at is the one that stops a message
reaching a stranger, and it should not need Postgres to be verified.
"""

from __future__ import annotations

import pytest

from app import services
from platform_core.errors import ValidationProblem


@pytest.mark.parametrize(
    ("supplied", "stored"),
    [
        ("+917976603446", "+917976603446"),
        ("+91 79766-03446", "+917976603446"),
        ("  +1 (555) 010-1234 ", "+15550101234"),
    ],
)
def test_separators_a_human_typed_are_stripped(supplied: str, stored: str) -> None:
    assert services._canonical_e164(supplied) == stored


def test_a_number_without_a_country_code_is_refused() -> None:
    """Guessing a country code sends a stranger someone else's invoice."""
    with pytest.raises(ValidationProblem, match="country code"):
        services._canonical_e164("7976603446")


@pytest.mark.parametrize("bad", ["+0123456789", "+1234", "+" + "9" * 20, "not a phone"])
def test_malformed_numbers_are_refused(bad: str) -> None:
    with pytest.raises(ValidationProblem):
        services._canonical_e164(bad)
