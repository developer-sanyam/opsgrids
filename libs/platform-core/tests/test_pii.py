"""The PII envelope: seal, open, mask, and rotate — with no database in sight.

These are the guarantees the "encrypted and masked by default" rule rests on, so they are
asserted as invariants rather than examples: the ciphertext is never the plaintext, a mask never
contains the whole secret, a value survives a key rotation, and a tampered envelope refuses
rather than returning something plausible.
"""

from __future__ import annotations

import hashlib

import pytest

from platform_core.config import ServiceSettings
from platform_core.pii import (
    PII_KINDS,
    PiiError,
    blind_index,
    mask,
    open_sealed,
    seal,
)


def _settings(
    *, key: str = "unit-test-pii-key", decrypt: list[str] | None = None
) -> ServiceSettings:
    """Settings with the PII keys set and no `.env` read, so a developer's real keys and a CI
    run cannot change what these assert."""
    return ServiceSettings(
        _env_file=None,
        service_name="probe",
        db_schema="public",
        pii_encryption_key=key,
        pii_decrypt_keys=decrypt or [],
    )


# Representative plaintext per kind — real-shaped so the maskers exercise their real branches.
_SAMPLES = {
    "email": "jane.doe@example.com",
    "phone": "9876543210",
    "pan": "ABCDE1234F",  # pragma: allowlist secret
    "aadhaar": "123412341234",
    "uan": "100200300400",
    "esi": "3100000000000000",
    "bank_account": "000123456789",
    "bank_ifsc": "HDFC0001234",
    "dob": "1990-05-15",
    "generic": "some-reference-value",
}


class TestRoundtrip:
    @pytest.mark.parametrize("kind", sorted(PII_KINDS))
    def test_a_sealed_value_opens_back_to_itself(self, kind: str) -> None:
        settings = _settings()
        plaintext = _SAMPLES[kind]
        ciphertext, _ = seal(plaintext, kind=kind, settings=settings)
        assert open_sealed(ciphertext, settings=settings) == plaintext

    def test_the_ciphertext_is_not_the_plaintext(self) -> None:
        settings = _settings()
        ciphertext, _ = seal("000123456789", kind="bank_account", settings=settings)
        assert b"000123456789" not in ciphertext

    def test_sealing_twice_differs_because_the_nonce_is_fresh(self) -> None:
        """No equality oracle: the same value sealed twice must not produce the same bytes, or a
        reader could tell two rows hold the same secret without ever decrypting either."""
        settings = _settings()
        first, _ = seal("123412341234", kind="aadhaar", settings=settings)
        second, _ = seal("123412341234", kind="aadhaar", settings=settings)
        assert first != second
        assert open_sealed(first, settings=settings) == open_sealed(second, settings=settings)


class TestMasking:
    @pytest.mark.parametrize("kind", sorted(PII_KINDS - {"bank_ifsc"}))
    def test_a_mask_never_contains_the_whole_secret(self, kind: str) -> None:
        """The point of the mask: recognisable, never disclosing. IFSC is exempt because it is a
        public branch code shown whole by design."""
        plaintext = _SAMPLES[kind]
        _, masked = seal(plaintext, kind=kind, settings=_settings())
        assert plaintext not in masked

    def test_the_email_mask_keeps_only_the_initials(self) -> None:
        assert mask("jane.doe@example.com", kind="email") == "j•••@e•••"

    def test_the_phone_mask_is_the_last_four(self) -> None:
        assert mask("9876543210", kind="phone") == "•••••• 3210"

    def test_the_aadhaar_mask_is_grouped_last_four(self) -> None:
        assert mask("123412341234", kind="aadhaar") == "•••• •••• 1234"

    def test_the_dob_mask_is_the_year_only(self) -> None:
        assert mask("1990-05-15", kind="dob") == "••/••/1990"

    def test_an_ifsc_is_shown_whole_because_it_is_not_secret(self) -> None:
        assert mask("HDFC0001234", kind="bank_ifsc") == "HDFC0001234"

    def test_an_unknown_kind_falls_back_to_generic_without_raising(self) -> None:
        assert mask("abcd", kind="not-a-real-kind") == "••cd"

    def test_a_short_input_masks_without_raising(self) -> None:
        """A one-character value must not index past its end — it masks to itself, revealing
        nothing more than there is."""
        assert mask("x", kind="bank_account") == "x"

    def test_an_empty_value_masks_to_empty(self) -> None:
        assert mask("", kind="pan") == ""


class TestRotation:
    def test_a_value_sealed_under_the_old_key_opens_after_rotation(self) -> None:
        """Rotation is operational, not a migration: seal under K1, then make K2 current and move
        K1 to the decrypt list. The old row still opens; new rows seal under K2."""
        old = _settings(key="key-one")
        ciphertext, _ = seal("000123456789", kind="bank_account", settings=old)

        rotated = _settings(key="key-two", decrypt=["key-one"])
        assert open_sealed(ciphertext, settings=rotated) == "000123456789"

        fresh, _ = seal("999888777666", kind="bank_account", settings=rotated)
        assert open_sealed(fresh, settings=rotated) == "999888777666"

    def test_a_value_whose_key_is_gone_cannot_be_opened(self) -> None:
        """Retire a key entirely and the values sealed under it stop opening — the point of
        retiring it, and proof `open_sealed` is not silently succeeding on the wrong key."""
        old = _settings(key="key-one")
        ciphertext, _ = seal("secret-value", kind="generic", settings=old)
        with pytest.raises(PiiError):
            open_sealed(ciphertext, settings=_settings(key="key-two"))


class TestTampering:
    def test_a_flipped_byte_refuses_rather_than_returns(self) -> None:
        settings = _settings()
        ciphertext, _ = seal("000123456789", kind="bank_account", settings=settings)
        tampered = bytearray(ciphertext)
        tampered[-1] ^= 0x01  # flip one bit of the GCM tag
        with pytest.raises(PiiError):
            open_sealed(bytes(tampered), settings=settings)

    def test_a_truncated_envelope_is_malformed(self) -> None:
        with pytest.raises(PiiError):
            open_sealed(b"\x01short", settings=_settings())

    def test_an_unknown_version_byte_is_malformed(self) -> None:
        settings = _settings()
        ciphertext, _ = seal("000123456789", kind="bank_account", settings=settings)
        wrong_version = bytes([0xFF]) + ciphertext[1:]
        with pytest.raises(PiiError):
            open_sealed(wrong_version, settings=settings)


class TestBlindIndex:
    """Finding a sealed value without decrypting it.

    The property that matters is a pair: deterministic enough to look up, keyed enough not to be
    reversible. Each test below breaks one half of that pair if the implementation is wrong.
    """

    def test_the_same_value_indexes_the_same_way(self) -> None:
        """Without this there is no lookup at all."""
        settings = _settings()
        a = blind_index("+917976603446", kind="phone", settings=settings)
        b = blind_index("+917976603446", kind="phone", settings=settings)
        assert a == b

    def test_different_values_index_differently(self) -> None:
        settings = _settings()
        a = blind_index("+917976603446", kind="phone", settings=settings)
        b = blind_index("+917976603447", kind="phone", settings=settings)
        assert a != b

    def test_the_index_does_not_contain_the_value(self) -> None:
        index = blind_index("+917976603446", kind="phone", settings=_settings())
        assert "7976603446" not in index
        assert "+91" not in index

    def test_the_index_is_not_an_unkeyed_hash(self) -> None:
        """The whole security argument. A phone number is a ten-digit space, so an unkeyed
        digest of one is reversible by brute force in seconds — publishing such a column would
        be publishing the numbers. This asserts the key is actually mixed in."""
        value = "+917976603446"
        index = blind_index(value, kind="phone", settings=_settings())
        assert index != hashlib.sha256(value.encode()).hexdigest()
        assert index != hashlib.sha256(f"phone:{value}".encode()).hexdigest()

    def test_a_different_key_gives_a_different_index(self) -> None:
        """Which is also why rotating the key invalidates stored indices — documented on the
        function, asserted here so nobody assumes otherwise."""
        one = blind_index("+917976603446", kind="phone", settings=_settings())
        two = blind_index(
            "+917976603446", kind="phone", settings=_settings(key="another-passphrase")
        )
        assert one != two

    def test_kinds_are_separated(self) -> None:
        """Two kinds holding an identical string still index differently, so an index cannot be
        matched across columns that happen to share a value."""
        settings = _settings()
        assert blind_index("same", kind="phone", settings=settings) != blind_index(
            "same", kind="email", settings=settings
        )

    def test_the_index_key_is_not_the_encryption_key(self) -> None:
        """Domain separation: two HKDF contexts over one passphrase, so a weakness found in one
        derivation does not hand over the other."""
        settings = _settings()
        sealed, _ = seal("+917976603446", kind="phone", settings=settings)
        index = blind_index("+917976603446", kind="phone", settings=settings)
        assert bytes.fromhex(index) not in sealed

    def test_rotation_does_not_rescue_a_stale_index(self) -> None:
        """`pii_decrypt_keys` keeps decryption working across a rotation; lookup has no such
        fallback, because an index is derived from the current key alone. A rotation plan has
        to rebuild them, and this is the assertion that says so."""
        old = blind_index("+917976603446", kind="phone", settings=_settings(key="old-key"))
        rotated = _settings(key="new-key", decrypt=["old-key"])
        assert blind_index("+917976603446", kind="phone", settings=rotated) != old


class TestAssociatedData:
    """`aad` binds a sealed value to the row it was written for — MINOR 8 of the 2026-09-24
    security review, added so a service can seal a secret with (e.g.) `mfa_factor:<user_id>`
    and have GCM itself refuse to open it under any other row's identity."""

    def test_a_value_sealed_with_aad_opens_with_the_same_aad(self) -> None:
        settings = _settings()
        sealed, _ = seal("JBSWY3DPEHPK3PXP", kind="generic", settings=settings, aad=b"user:1")
        assert open_sealed(sealed, settings=settings, aad=b"user:1") == "JBSWY3DPEHPK3PXP"

    def test_the_wrong_aad_refuses_to_open_even_with_the_right_key(self) -> None:
        settings = _settings()
        sealed, _ = seal("JBSWY3DPEHPK3PXP", kind="generic", settings=settings, aad=b"user:1")
        with pytest.raises(PiiError):
            open_sealed(sealed, settings=settings, aad=b"user:2")

    def test_no_aad_at_open_refuses_a_value_sealed_with_one(self) -> None:
        settings = _settings()
        sealed, _ = seal("JBSWY3DPEHPK3PXP", kind="generic", settings=settings, aad=b"user:1")
        with pytest.raises(PiiError):
            open_sealed(sealed, settings=settings)

    def test_aad_is_optional_and_backward_compatible(self) -> None:
        """Every existing caller omits `aad` — sealing and opening with neither must behave
        exactly as it did before this parameter existed."""
        settings = _settings()
        sealed, _ = seal("JBSWY3DPEHPK3PXP", kind="generic", settings=settings)
        assert open_sealed(sealed, settings=settings) == "JBSWY3DPEHPK3PXP"
