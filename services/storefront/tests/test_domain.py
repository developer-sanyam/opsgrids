"""The pure rules: postal codes, the promise, the CSV, and the `SLOTTED` refusal.

No database, no session, no clock — every test that involves time pins one, because a promised
date that depends on when the suite runs is a test that fails at midnight in one timezone.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app import domain


class TestPostalCodes:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("400050", "400050"),
            ("400 050", "400050"),
            ("400-050", "400050"),
            (" 400050 ", "400050"),
        ],
    )
    def test_spellings_of_one_area_normalise_to_one_code(self, raw: str, expected: str) -> None:
        """This is what makes `UNIQUE (organization_id, postal_code)` mean what a merchant
        thinks it means: two spellings cannot end up in two zones."""
        assert domain.validate_postal_code(raw) == expected

    @pytest.mark.parametrize("raw", ["40005", "4000501", "ABCDEF", "000050", "", "   "])
    def test_a_malformed_code_is_a_rejection_not_an_answer(self, raw: str) -> None:
        """ "That is not a postal code" and "we do not deliver there" are different facts.

        Collapsing them would teach a buyer that a typo means the shop does not reach them.
        """
        with pytest.raises(domain.Rejected) as exc:
            domain.validate_postal_code(raw)
        assert exc.value.status_code == 422

    def test_the_validator_has_exactly_one_country(self) -> None:
        """One country, one entry — not generalised, because a generalisation with one use case
        is a guess (ADR-0010 §2). The *column* is already wide enough for a second."""
        assert set(domain._POSTAL_CODE_PATTERNS) == {"IN"}


class TestThePromise:
    """The server computes the date, in the **store's** timezone, never the browser's."""

    def test_before_the_cutoff_promises_today_plus_the_offset(self) -> None:
        # 08:30 UTC is 14:00 in Mumbai, before an 18:00 cutoff.
        now = dt.datetime(2026, 9, 24, 8, 30, tzinfo=dt.UTC)
        assert domain.resolve_promise_date(
            now=now, timezone="Asia/Kolkata", offset_days=1, cutoff=dt.time(18, 0)
        ) == dt.date(2026, 9, 25)

    def test_after_the_cutoff_promises_one_more_day(self) -> None:
        # 13:30 UTC is 19:00 in Mumbai, past the same cutoff.
        now = dt.datetime(2026, 9, 24, 13, 30, tzinfo=dt.UTC)
        assert domain.resolve_promise_date(
            now=now, timezone="Asia/Kolkata", offset_days=1, cutoff=dt.time(18, 0)
        ) == dt.date(2026, 9, 26)

    def test_the_store_timezone_decides_the_day_not_utc(self) -> None:
        """20:00 UTC is already the next day in Mumbai. A buyer in Singapore looking at a Mumbai
        store must see the Mumbai answer (ADR-0000 "Also settled")."""
        now = dt.datetime(2026, 9, 24, 20, 0, tzinfo=dt.UTC)
        assert domain.today_in("Asia/Kolkata", now=now) == dt.date(2026, 9, 25)
        assert domain.today_in("UTC", now=now) == dt.date(2026, 9, 24)

    def test_an_unknown_timezone_is_refused_rather_than_silently_utc(self) -> None:
        with pytest.raises(domain.Rejected):
            domain.today_in("Mars/Olympus_Mons", now=dt.datetime(2026, 9, 24, tzinfo=dt.UTC))


class TestZoneRules:
    def test_slotted_is_refused_with_a_message_naming_the_next_slice(self) -> None:
        """A half-rendered slot picker in front of a real buyer is worse than a feature that is
        honestly absent (ADR-0010 §3). The value stays in the enum so slice 2 is a validation
        change rather than a schema change."""
        with pytest.raises(domain.Rejected) as exc:
            domain.assert_mode_is_available("SLOTTED")
        assert exc.value.code == "mode_not_available"
        assert "next slice" in exc.value.message

    @pytest.mark.parametrize("mode", ["PROMISED", "UNSERVED"])
    def test_the_two_slice_one_modes_are_available(self, mode: str) -> None:
        domain.assert_mode_is_available(mode)

    def test_a_promised_zone_needs_a_promise(self) -> None:
        with pytest.raises(domain.Rejected):
            domain.validate_promise(
                "PROMISED",
                domain.PromiseInput(kind=None, minutes=None, offset_days=None, cutoff=None),
            )

    def test_a_duration_promise_needs_minutes(self) -> None:
        with pytest.raises(domain.Rejected):
            domain.validate_promise(
                "PROMISED",
                domain.PromiseInput(kind="DURATION", minutes=None, offset_days=None, cutoff=None),
            )

    def test_a_date_promise_needs_an_offset_and_a_cutoff(self) -> None:
        with pytest.raises(domain.Rejected):
            domain.validate_promise(
                "PROMISED",
                domain.PromiseInput(kind="DATE_OFFSET", minutes=None, offset_days=1, cutoff=None),
            )

    def test_an_unserved_zone_may_not_carry_a_promise(self) -> None:
        with pytest.raises(domain.Rejected):
            domain.validate_promise(
                "UNSERVED",
                domain.PromiseInput(kind="DURATION", minutes=30, offset_days=None, cutoff=None),
            )


class TestTheTextarea:
    def test_line_and_comma_separated_both_parse(self) -> None:
        assert domain.parse_postal_code_list("400050\n400051, 400052") == [
            "400050",
            "400051",
            "400052",
        ]

    def test_a_duplicate_within_one_zone_is_de_duplicated_silently(self) -> None:
        """A merchant pasting a column can see what they pasted, and the same code twice in one
        zone means the same thing twice. The CSV is where a duplicate is a contradiction."""
        assert domain.parse_postal_code_list("400050\n400050") == ["400050"]


class TestTheImportCsv:
    def test_a_duplicate_postal_code_is_refused_naming_the_lines(self) -> None:
        """**Last-wins on a file the merchant cannot read back is the silent wrong answer**, and
        a silent wrong answer in 19,000 rows is found by a customer rather than by the merchant
        (ADR-0010 §9). Detected in pure code, before any SQL."""
        body = "postal_code,zone_code\n400050,A\n400051,A\n400050,B\n"
        with pytest.raises(domain.Rejected) as exc:
            domain.parse_import_csv(body)
        assert exc.value.code == "import_duplicate_postal_code"
        assert exc.value.lines == [2, 4]

    def test_a_clean_file_parses_to_rows_with_their_zone_codes(self) -> None:
        parsed = domain.parse_import_csv("postal_code,zone_code\n400050,BANDRA\n400 051,ANDHERI\n")
        assert [(r.postal_code, r.zone_code) for r in parsed.rows] == [
            ("400050", "BANDRA"),
            ("400051", "ANDHERI"),
        ]
        assert parsed.zone_codes == {"BANDRA", "ANDHERI"}

    def test_a_wrong_header_is_refused(self) -> None:
        with pytest.raises(domain.Rejected) as exc:
            domain.parse_import_csv("pincode,zone\n400050,A\n")
        assert exc.value.code == "import_header_invalid"

    def test_an_invalid_postal_code_refuses_the_whole_file(self) -> None:
        """All or nothing: a half-applied import is worse than a failed one, because the merchant
        cannot tell which half."""
        with pytest.raises(domain.Rejected) as exc:
            domain.parse_import_csv("postal_code,zone_code\n400050,A\nnope,A\n")
        assert exc.value.code == "import_postal_code_invalid"
        assert exc.value.lines == [3]

    def test_a_blank_zone_code_is_refused(self) -> None:
        with pytest.raises(domain.Rejected) as exc:
            domain.parse_import_csv("postal_code,zone_code\n400050,\n")
        assert exc.value.code == "import_zone_code_missing"

    def test_blank_rows_are_ignored_rather_than_refused(self) -> None:
        """A spreadsheet export ends with a blank line, and refusing that would refuse most
        real files for a reason the merchant cannot see."""
        parsed = domain.parse_import_csv("postal_code,zone_code\n400050,A\n\n\n")
        assert len(parsed.rows) == 1

    def test_an_empty_file_is_refused(self) -> None:
        with pytest.raises(domain.Rejected):
            domain.parse_import_csv("")
