"""GSTIN parsing and its checksum — the identity every filing rests on.

These are pure-function tests with no database, because that is what the value object is for: a
GSTIN is validated once, at the boundary, and everything downstream can then assume it. The
checksum cases are the point of the file. Length and structure catch a paste error; the check
digit is what catches a *transposition* — 27AAPFU0939F1ZV typed as 27AAPFU9039F1ZV — which every
free-text field on earth accepts and the tax portal rejects months later as a return mismatch.

The two known-good numbers below are real: `27AAPFU0939F1ZV` is GSTN's own published example and
`09AAACH7409R1ZZ` is a public company registration. They are here so the algorithm is anchored to
something outside this repository rather than to its own output.
"""

from __future__ import annotations

import pytest

from app import domain

# Public, verifiable GSTINs — the anchor for the checksum implementation.
KNOWN_GOOD = (
    "27AAPFU0939F1ZV",
    "09AAACH7409R1ZZ",
)


class TestCheckDigit:
    @pytest.mark.parametrize("gstin", KNOWN_GOOD)
    def test_the_published_examples_verify(self, gstin: str) -> None:
        assert domain.gstin_check_digit(gstin[:14]) == gstin[14]

    def test_a_check_digit_needs_exactly_fourteen_characters(self) -> None:
        with pytest.raises(domain.GstinError, match="exactly 14"):
            domain.gstin_check_digit("27AAPFU0939F1")

    def test_a_character_outside_the_alphabet_is_refused(self) -> None:
        with pytest.raises(domain.GstinError, match="not a character"):
            domain.gstin_check_digit("27AAPFU0939F1-")

    def test_the_check_digit_is_one_of_the_thirty_six(self) -> None:
        """A property, over every state code: the algorithm can only ever emit an alphabet
        character, so a caller may compare it to a GSTIN's 15th position directly."""
        for state in sorted(domain.GST_STATE_CODES):
            base = f"{state}AAPFU0939F1Z"
            assert domain.gstin_check_digit(base) in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class TestParsing:
    @pytest.mark.parametrize("gstin", KNOWN_GOOD)
    def test_a_valid_gstin_parses_into_its_parts(self, gstin: str) -> None:
        parsed = domain.parse_gstin(gstin)
        assert parsed.value == gstin
        assert parsed.state_code == gstin[:2]
        assert parsed.pan == gstin[2:12]
        assert parsed.entity_code == gstin[12]
        assert parsed.check_digit == gstin[14]
        assert parsed.state_name  # every code in the table has a name
        assert str(parsed) == gstin

    def test_spaces_hyphens_and_case_are_normalised(self) -> None:
        assert domain.parse_gstin(" 27aapfu0939f1zv ").value == "27AAPFU0939F1ZV"
        assert domain.parse_gstin("27-AAPFU0939F1ZV").value == "27AAPFU0939F1ZV"

    def test_the_wrong_length_is_refused_by_length(self) -> None:
        with pytest.raises(domain.GstinError, match="15 characters"):
            domain.parse_gstin("27AAPFU0939F1Z")

    def test_a_malformed_body_is_refused_by_shape(self) -> None:
        # Digits where the PAN's five leading letters belong. Right length, wrong shape.
        with pytest.raises(domain.GstinError, match="not shaped like"):
            domain.parse_gstin("27123450939F1ZV")

    def test_the_fourteenth_character_must_be_z(self) -> None:
        """UIN and OIDAR registrations put something else there. They are refused loudly rather
        than accepted into a pipeline that would then have to special-case them."""
        with pytest.raises(domain.GstinError, match="not shaped like"):
            domain.parse_gstin("27AAPFU0939F1AV")

    def test_an_unknown_state_code_is_refused(self) -> None:
        base = "50AAPFU0939F1Z"
        candidate = base + domain.gstin_check_digit(base)
        with pytest.raises(domain.GstinError, match="not a GST state code"):
            domain.parse_gstin(candidate)

    def test_the_legacy_state_codes_still_parse(self) -> None:
        """25 (Daman and Diu) and 28 (undivided Andhra Pradesh) were retired by reorganisations,
        and registrations issued under them are still in circulation."""
        for state in ("25", "28"):
            base = f"{state}AAPFU0939F1Z"
            parsed = domain.parse_gstin(base + domain.gstin_check_digit(base))
            assert parsed.state_code == state


class TestChecksumFailures:
    @pytest.mark.parametrize("gstin", KNOWN_GOOD)
    def test_every_single_character_corruption_is_caught(self, gstin: str) -> None:
        """The whole reason a checksum exists. Change any one character of a valid GSTIN to any
        other alphabet character and the number must stop validating — otherwise a typo reaches a
        return."""
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        survivors: list[str] = []
        for position in range(15):
            for char in alphabet:
                if char == gstin[position]:
                    continue
                corrupted = gstin[:position] + char + gstin[position + 1 :]
                if domain.is_valid_gstin(corrupted):
                    survivors.append(corrupted)
        assert survivors == []

    @pytest.mark.parametrize("gstin", KNOWN_GOOD)
    def test_adjacent_transpositions_are_caught(self, gstin: str) -> None:
        """The error a weighted-sum check digit misses and a folded one does not."""
        for i in range(14):
            if gstin[i] == gstin[i + 1]:
                continue
            swapped = gstin[:i] + gstin[i + 1] + gstin[i] + gstin[i + 2 :]
            assert not domain.is_valid_gstin(swapped), swapped

    def test_the_reason_names_the_expected_check_digit(self) -> None:
        wrong = "27AAPFU0939F1ZA"
        with pytest.raises(domain.GstinError, match="should be 'V'"):
            domain.parse_gstin(wrong)

    @pytest.mark.parametrize(
        "candidate",
        ["", "   ", "not-a-gstin", "27AAPFU0939F1Z", "27AAPFU0939F1ZVV", "27AAPFU0939F0ZV"],
    )
    def test_is_valid_gstin_is_a_predicate_not_a_raise(self, candidate: str) -> None:
        assert domain.is_valid_gstin(candidate) is False


class TestStateNames:
    def test_the_everyday_spellings_resolve(self) -> None:
        assert domain.state_code_for_name("Maharashtra") == "27"
        assert domain.state_code_for_name("tamil nadu") == "33"
        assert domain.state_code_for_name("  Delhi  ") == "07"
        assert domain.state_code_for_name("New Delhi") == "07"
        assert domain.state_code_for_name("Orissa") == "21"

    def test_an_unknown_name_resolves_to_nothing_rather_than_a_guess(self) -> None:
        """A wrong place of supply picks a wrong tax head, which is a refund claim rather than a
        rounding difference. None makes it somebody's job to fix."""
        assert domain.state_code_for_name("Atlantis") is None
        assert domain.state_code_for_name("") is None
        assert domain.state_code_for_name(None) is None

    def test_every_state_name_round_trips_to_its_own_code(self) -> None:
        for code, name in domain.GST_STATE_CODES.items():
            assert domain.state_code_for_name(name) == code


class TestFinancialYear:
    @pytest.mark.parametrize(
        "iso,expected",
        [
            ("2026-04-01", "2026-27"),
            ("2026-08-24", "2026-27"),
            ("2027-03-31", "2026-27"),
            ("2026-03-31", "2025-26"),
            ("2026-01-01", "2025-26"),
        ],
    )
    def test_april_to_march(self, iso: str, expected: str) -> None:
        from datetime import date

        assert domain.financial_year(date.fromisoformat(iso)) == expected


class TestHsn:
    @pytest.mark.parametrize("code", ["7208", "720851", "99651100", "996511"])
    def test_the_published_lengths_are_accepted(self, code: str) -> None:
        assert domain.normalise_hsn(code) == code

    def test_separators_are_stripped(self) -> None:
        assert domain.normalise_hsn(" 7208-51 ") == "720851"

    def test_absent_stays_absent(self) -> None:
        assert domain.normalise_hsn(None) is None
        assert domain.normalise_hsn("  ") is None

    @pytest.mark.parametrize("code", ["72", "72085", "7208511", "72A851"])
    def test_anything_else_is_refused(self, code: str) -> None:
        with pytest.raises(ValueError, match="HSN/SAC"):
            domain.normalise_hsn(code)
