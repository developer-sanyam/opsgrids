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


SUFFIX = "stores.opsgrids.com"


class TestTheHostABuyerArrivesOn:
    """A hostname from the open internet, reduced to one comparable form or refused.

    This is the only untrusted string on the bootstrap path and it becomes a Redis key, so the
    two halves of the rule are tested together: what it *accepts* it also **bounds**, and what it
    cannot parse it refuses at the boundary rather than carrying into a lookup.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("merchant.com", "merchant.com"),
            # `Host` is case-insensitive, and a browser will send whatever was typed.
            ("MERCHANT.COM", "merchant.com"),
            ("Merchant.Com", "merchant.com"),
            # A fully-qualified name carries the root dot.
            ("merchant.com.", "merchant.com"),
            # A non-default port is appended to the Host header; it is not part of the name.
            ("merchant.com:8443", "merchant.com"),
            ("merchant.com.:443", "merchant.com"),
            # Whatever the bundle read out of `location.hostname`, trimmed.
            ("  merchant.com  ", "merchant.com"),
            # The platform's own host, which is the common case.
            ("greenleaf.stores.opsgrids.com", "greenleaf.stores.opsgrids.com"),
            # U-label → A-label: the form the browser actually resolves and the form the
            # column can hold, since `custom_domain_shape` is ASCII-only.
            ("münchen.de", "xn--mnchen-3ya.de"),
            ("日本.jp", "xn--wgv71a.jp"),
            # Already punycode: idempotent, which matters because the value is a cache key.
            ("xn--mnchen-3ya.de", "xn--mnchen-3ya.de"),
        ],
    )
    def test_the_forms_a_browser_sends_normalise_to_one(self, raw: str, expected: str) -> None:
        assert domain.normalise_request_host(raw) == expected

    def test_normalising_is_idempotent(self) -> None:
        """It has to be: the output is a cache key, and a value that normalises to a second
        value the second time would write two entries for one store and invalidate one."""
        once = domain.normalise_request_host("MÜNCHEN.de.:443")
        assert domain.normalise_request_host(once) == once

    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            ("", "host_required"),
            ("   ", "host_required"),
            # A pasted URL rather than a hostname.
            ("https://merchant.com", "host_invalid"),
            ("merchant.com/shop", "host_invalid"),
            ("user@merchant.com", "host_invalid"),
            ("mer chant.com", "host_invalid"),
            ("*.merchant.com", "host_invalid"),
            # A single label cannot be a store's address and cannot be claimed.
            ("localhost", "host_invalid"),
            ("merchant", "host_invalid"),
            # No store is reached by address, and the edge issues no certificate for one.
            ("198.51.100.7", "host_invalid"),
            ("[::1]", "host_invalid"),
            ("[2001:db8::1]:443", "host_invalid"),
            ("::1", "host_invalid"),
            # A port that is not a number is not a port.
            ("merchant.com:https", "host_invalid"),
            (":443", "host_invalid"),
            # Shapes DNS itself refuses.
            ("-merchant.com", "host_invalid"),
            ("merchant-.com", "host_invalid"),
            ("merchant..com", "host_invalid"),
            (".merchant.com", "host_invalid"),
            ("under_score.com", "host_invalid"),
            (f"{'a' * 64}.com", "host_invalid"),
            # The bound, checked before anything expensive runs.
            (f"{'a' * 250}.com", "host_too_long"),
        ],
    )
    def test_what_it_refuses_and_what_it_calls_the_refusal(self, raw: str, code: str) -> None:
        """**Every one of these is a 422, not a 404.** "That is not an address" and "nothing is
        published there" are different facts, exactly as a malformed postal code is distinct from
        an unserved one — and a client that cannot tell them apart shows a buyer the wrong page.
        """
        with pytest.raises(domain.Rejected) as caught:
            domain.normalise_request_host(raw)
        assert caught.value.code == code
        assert caught.value.errors[0]["field"] == "host"

    def test_a_refusal_never_echoes_the_string_it_refused(self) -> None:
        """The message is a sentence, not a mirror. Reflecting an attacker-supplied host into a
        response body is how a problem document becomes an injection vector for whoever renders
        it."""
        with pytest.raises(domain.Rejected) as caught:
            domain.normalise_request_host("<script>alert(1)</script>.com")
        assert "script" not in caught.value.message

    def test_a_reserved_suffix_is_not_refused_here(self) -> None:
        """`RESERVED_DOMAIN_SUFFIXES` governs what a merchant may *claim*. The platform's own
        subdomain is the most common host this function will ever see, and refusing it would
        404 every store on the address it is permanently reachable at."""
        assert domain.normalise_request_host("greenleaf.stores.opsgrids.com") == (
            "greenleaf.stores.opsgrids.com"
        )


class TestTheTwoAddressesOfOneStore:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("greenleaf.stores.opsgrids.com", "greenleaf"),
            ("a-b-1.stores.opsgrids.com", "a-b-1"),
            # Not the platform's space at all.
            ("merchant.com", None),
            ("greenleaf.stores.opsgrids.com.evil.com", None),
            # The suffix itself is not a store.
            ("stores.opsgrids.com", None),
            # **Exactly one label.** A wildcard certificate covers one level, and `a.b` is a
            # name no slug column can hold — reading it as a slug would turn an impossible
            # value into a lookup that merely missed.
            ("a.b.stores.opsgrids.com", None),
            # A label that cannot be a slug misses without a query.
            ("-bad.stores.opsgrids.com", None),
            ("x.stores.opsgrids.com", None),
        ],
    )
    def test_a_platform_host_is_split_back_into_its_slug(
        self, host: str, expected: str | None
    ) -> None:
        assert domain.platform_slug(host, suffix=SUFFIX) == expected

    def test_the_platform_host_round_trips(self) -> None:
        built = domain.platform_host_for("greenleaf", suffix=SUFFIX)
        assert built == "greenleaf.stores.opsgrids.com"
        assert domain.platform_slug(built, suffix=SUFFIX) == "greenleaf"

    @pytest.mark.parametrize(
        ("host", "partner"),
        [
            ("merchant.com", "www.merchant.com"),
            ("www.merchant.com", "merchant.com"),
            ("shop.merchant.com", "www.shop.merchant.com"),
        ],
    )
    def test_the_www_pair_is_purely_syntactic(self, host: str, partner: str) -> None:
        """No Public Suffix List: a bundled list goes stale on somebody else's schedule, and the
        residual it buys is named and small (ADR-0012 §2)."""
        assert domain.www_partner(host) == partner


class TestWhichAddressIsCanonical:
    """ADR-0012 §8. The merchant's domain the moment it is live, the platform host otherwise —
    and **the platform host again the moment the domain stops working**, because a canonical
    pointer at a name that does not answer is a redirect into a hole."""

    def test_a_store_with_no_domain_is_canonical_at_the_platform_host(self) -> None:
        assert (
            domain.canonical_host(
                slug="greenleaf",
                custom_domain=None,
                custom_domain_status=None,
                suffix=SUFFIX,
            )
            == "greenleaf.stores.opsgrids.com"
        )

    def test_a_live_domain_is_canonical(self) -> None:
        assert (
            domain.canonical_host(
                slug="greenleaf",
                custom_domain="merchant.com",
                custom_domain_status="live",
                suffix=SUFFIX,
            )
            == "merchant.com"
        )

    @pytest.mark.parametrize("status", ["unverified", "verified", "failing", "suspended"])
    def test_every_state_short_of_live_leaves_the_platform_host_canonical(
        self, status: str
    ) -> None:
        """`verified` is the cutover state — the claim is proved, the DNS has not landed yet —
        and pointing a canonical link at it would aim search engines at a name that does not
        resolve. `failing` is the same fact after the fact, which is exactly why the fallback
        works: it only works if it is not also redirecting."""
        assert (
            domain.canonical_host(
                slug="greenleaf",
                custom_domain="merchant.com",
                custom_domain_status=status,
                suffix=SUFFIX,
            )
            == "greenleaf.stores.opsgrids.com"
        )

    def test_an_unverified_claim_is_not_a_status_that_serves(self) -> None:
        """The list the host lookup filters on. `unverified` being absent is the important
        entry: a claim is stored on the word of whoever typed it."""
        assert "unverified" not in domain.SERVING_DOMAIN_STATUSES
        assert "verified" in domain.SERVING_DOMAIN_STATUSES
