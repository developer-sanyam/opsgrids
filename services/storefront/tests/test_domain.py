"""The pure rules: postal codes, the promise, the CSV, and the `SLOTTED` refusal.

No database, no session, no clock — every test that involves time pins one, because a promised
date that depends on when the suite runs is a test that fails at midnight in one timezone.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

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


# --------------------------------------------------------------------- the domain claim state


class TestVerificationTexts:
    def test_the_token_is_32_hex_characters(self) -> None:
        """`secrets.token_hex(16)` — 128 bits (ADR-0012 §3)."""
        token = domain.mint_verification_token()
        assert len(token) == 32
        int(token, 16)  # does not raise

    def test_two_mints_never_collide_in_a_reasonable_sample(self) -> None:
        assert len({domain.mint_verification_token() for _ in range(1000)}) == 1000

    def test_the_txt_name_and_value_are_what_the_probe_looks_for(self) -> None:
        assert domain.verification_txt_name("merchant.com") == "_opsgrids-challenge.merchant.com"
        assert domain.verification_txt_value("abc123") == "opsgrids-domain-verification=abc123"


class TestTheUnverifiedBackoff:
    """ADR-0012 §6: 60s for 15 minutes, then 5 minutes, then 30 minutes, then hourly, until the
    14-day expiry."""

    def test_the_first_fifteen_minutes_are_checked_every_sixty_seconds(self) -> None:
        assert domain.unverified_check_interval(dt.timedelta(0)) == dt.timedelta(seconds=60)
        assert domain.unverified_check_interval(
            dt.timedelta(minutes=14, seconds=59)
        ) == dt.timedelta(seconds=60)

    def test_the_next_stage_is_five_minutes(self) -> None:
        assert domain.unverified_check_interval(dt.timedelta(minutes=15)) == dt.timedelta(minutes=5)
        assert domain.unverified_check_interval(dt.timedelta(minutes=45)) == dt.timedelta(minutes=5)

    def test_the_next_stage_is_thirty_minutes(self) -> None:
        assert domain.unverified_check_interval(dt.timedelta(hours=1, minutes=15)) == dt.timedelta(
            minutes=30
        )

    def test_it_settles_at_hourly_and_stays_there(self) -> None:
        assert domain.unverified_check_interval(dt.timedelta(hours=26)) == dt.timedelta(hours=1)
        assert domain.unverified_check_interval(dt.timedelta(days=13, hours=23)) == dt.timedelta(
            hours=1
        )

    @given(
        st.floats(min_value=0, max_value=domain.CLAIM_EXPIRY.total_seconds() * 2, allow_nan=False)
    )
    def test_the_schedule_never_gets_shorter_as_time_passes(self, later_offset: float) -> None:
        """A property, not a table of examples: for any two elapsed durations where the second
        is later than the first, the interval the later one gets is never shorter — "the
        schedule only ever relaxes", the sentence the docstring promises rather than merely
        asserts."""
        earlier = dt.timedelta(seconds=0)
        later = earlier + dt.timedelta(seconds=later_offset)
        assert domain.unverified_check_interval(later) >= domain.unverified_check_interval(earlier)


class TestClaimExpiry:
    def test_a_claim_expires_fourteen_days_after_its_token_was_issued(self) -> None:
        issued = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        assert domain.claim_expires_at(issued) == dt.datetime(2026, 1, 15, tzinfo=dt.UTC)

    def test_expiry_is_a_closed_boundary(self) -> None:
        expires = dt.datetime(2026, 1, 15, tzinfo=dt.UTC)
        assert domain.is_claim_expired(expires, now=expires) is True
        assert domain.is_claim_expired(expires, now=expires - dt.timedelta(seconds=1)) is False


class TestPromotionFromUnverified:
    def test_a_successful_ownership_check_promotes_to_verified(self) -> None:
        assert domain.promote_from_unverified(ownership_ok=True) == "verified"

    def test_a_failed_one_leaves_it_unverified(self) -> None:
        assert domain.promote_from_unverified(ownership_ok=False) == "unverified"


#: `advance_verified_or_later`'s defaults for the common case a test does not care about:
#: publishing, never-suspended, already `live` once, and today's first check.
_DAY_ONE = dt.date(2026, 1, 1)


def _advance(
    *,
    store_published: bool = True,
    was_suspended: bool = False,
    has_been_live: bool = True,
    ownership_ok: bool,
    routing_ok: bool,
    certificate_ok: bool,
    consecutive_failures: int = 0,
    last_failure_counted_on: dt.date | None = None,
    today: dt.date = _DAY_ONE,
) -> domain.ClaimAdvance:
    return domain.advance_verified_or_later(
        store_published=store_published,
        was_suspended=was_suspended,
        has_been_live=has_been_live,
        ownership_ok=ownership_ok,
        routing_ok=routing_ok,
        certificate_ok=certificate_ok,
        consecutive_failures=consecutive_failures,
        last_failure_counted_on=last_failure_counted_on,
        today=today,
    )


class TestAdvanceVerifiedOrLater:
    """ADR-0012 §6, §7. `store_published=False` is the manual kill switch and is checked before
    anything else; every other failure gets the seven-day tolerance."""

    def test_all_three_probes_ok_reaches_live_and_resets_the_counter(self) -> None:
        result = _advance(
            ownership_ok=True, routing_ok=True, certificate_ok=True, consecutive_failures=5
        )
        assert result.status == "live"
        assert result.consecutive_failures == 0
        assert result.last_failure_counted_on is None

    @pytest.mark.parametrize(
        ("ownership_ok", "routing_ok", "certificate_ok"),
        [(False, True, True), (True, False, True), (True, True, False)],
    )
    def test_any_single_probe_failing_is_failing_not_suspended(
        self, ownership_ok: bool, routing_ok: bool, certificate_ok: bool
    ) -> None:
        result = _advance(
            ownership_ok=ownership_ok, routing_ok=routing_ok, certificate_ok=certificate_ok
        )
        assert result.status == "failing"
        assert result.consecutive_failures == 1
        assert result.last_failure_counted_on == _DAY_ONE

    def test_the_seventh_consecutive_failure_suspends(self) -> None:
        result = _advance(
            ownership_ok=False,
            routing_ok=True,
            certificate_ok=True,
            consecutive_failures=domain.SUSPEND_AFTER_CONSECUTIVE_FAILURES - 1,
            last_failure_counted_on=_DAY_ONE - dt.timedelta(days=1),
        )
        assert result.status == "suspended"
        assert result.consecutive_failures == domain.SUSPEND_AFTER_CONSECUTIVE_FAILURES

    def test_the_sixth_consecutive_failure_is_still_only_failing(self) -> None:
        """Generous on purpose (ADR-0012 §6): a DNS provider's bad day must not take a
        merchant's shop off the air before the seventh day."""
        result = _advance(
            ownership_ok=False,
            routing_ok=True,
            certificate_ok=True,
            consecutive_failures=domain.SUSPEND_AFTER_CONSECUTIVE_FAILURES - 2,
            last_failure_counted_on=_DAY_ONE - dt.timedelta(days=1),
        )
        assert result.status == "failing"

    def test_an_unpublished_store_suspends_immediately_regardless_of_the_failure_count(
        self,
    ) -> None:
        """ADR-0011's `unpublish` is the manual kill switch (ADR-0012 §7) — a merchant who
        switched their own shop off must not wait seven days for the domain to follow it."""
        result = _advance(
            store_published=False, ownership_ok=True, routing_ok=True, certificate_ok=True
        )
        assert result.status == "suspended"

    def test_recovery_resets_the_counter_rather_than_carrying_it_forward(self) -> None:
        """A domain that failed for six days and then answers cleanly is not one failure away
        from suspension the next time something hiccups."""
        result = _advance(
            ownership_ok=True, routing_ok=True, certificate_ok=True, consecutive_failures=6
        )
        assert result.consecutive_failures == 0


class TestAFailureCountsAtMostOncePerDay:
    """ADR-0012 review finding 4: "seven consecutive **daily** failures" (§6) means the sweep's
    once-a-day cadence, not the number of times anything calls this function. `POST
    .../domain/check` runs the same probes on demand, rate-limited to once per 30 seconds — a
    merchant leaning on "Check now" could otherwise suspend their own domain in a few minutes."""

    def test_a_second_failing_call_the_same_day_does_not_advance_the_counter(self) -> None:
        first = _advance(ownership_ok=False, routing_ok=True, certificate_ok=True)
        assert first.consecutive_failures == 1
        assert first.last_failure_counted_on == _DAY_ONE

        second = _advance(
            ownership_ok=False,
            routing_ok=True,
            certificate_ok=True,
            consecutive_failures=first.consecutive_failures,
            last_failure_counted_on=first.last_failure_counted_on,
        )
        assert second.consecutive_failures == 1
        assert second.status == "failing"

    def test_seven_manual_checks_in_one_day_never_suspends(self) -> None:
        """The bug, made concrete: seven calls, same day, same claim."""
        failures = 0
        last_counted: dt.date | None = None
        status = "verified"
        for _ in range(7):
            result = _advance(
                ownership_ok=False,
                routing_ok=True,
                certificate_ok=True,
                consecutive_failures=failures,
                last_failure_counted_on=last_counted,
            )
            failures = result.consecutive_failures
            last_counted = result.last_failure_counted_on
            status = result.status
        assert failures == 1
        assert status == "failing"

    def test_a_failure_on_a_new_day_does_advance_the_counter(self) -> None:
        result = _advance(
            ownership_ok=False,
            routing_ok=True,
            certificate_ok=True,
            consecutive_failures=1,
            last_failure_counted_on=_DAY_ONE,
            today=_DAY_ONE + dt.timedelta(days=1),
        )
        assert result.consecutive_failures == 2
        assert result.last_failure_counted_on == _DAY_ONE + dt.timedelta(days=1)


class TestRecoveryFromSuspension:
    """ADR-0012 review finding 5: `suspended` blocks `ask` (§5), so a claim suspended by the
    failure count can never again pass its certificate probe — "all three probes ok" is
    unreachable from `suspended` by construction. Recovery has to be its own path."""

    def test_ownership_and_routing_ok_moves_a_suspended_claim_to_verified_not_live(self) -> None:
        """Not straight to `live`: no certificate exists yet, because `ask` only just started
        saying yes again. The *next* check, once Caddy has issued one, takes it the rest of the
        way through the ordinary verified -> live branch."""
        result = _advance(
            was_suspended=True,
            ownership_ok=True,
            routing_ok=True,
            certificate_ok=False,
            consecutive_failures=9,
            last_failure_counted_on=_DAY_ONE,
        )
        assert result.status == "verified"
        assert result.consecutive_failures == 0
        assert result.last_failure_counted_on is None

    def test_ownership_still_failing_leaves_a_suspended_claim_suspended(self) -> None:
        """Recovery needs *both* ownership and routing back; a still-missing TXT record is not
        recovery, whatever the certificate probe (which does not even run) would have said."""
        result = _advance(
            was_suspended=True,
            ownership_ok=False,
            routing_ok=True,
            certificate_ok=False,
            consecutive_failures=9,
            last_failure_counted_on=_DAY_ONE,
        )
        assert result.status == "suspended"

    def test_a_claim_not_suspended_does_not_take_the_recovery_path(self) -> None:
        """`was_suspended=False` — an ordinary `failing` claim with two probes ok still needs
        all three to reach `live`; it does not get shortcut to `verified` on the way."""
        result = _advance(
            was_suspended=False,
            ownership_ok=True,
            routing_ok=True,
            certificate_ok=False,
            consecutive_failures=2,
            last_failure_counted_on=_DAY_ONE,
        )
        assert result.status == "failing"


class TestARoutingFailureBeforeGoingLiveIsNotSuspendable:
    """ADR-0012 review finding 5: cutover — the gap between "ownership proved" and "the merchant
    actually pointed DNS here" — is normal operation (§4), sometimes for a long time, not an
    outage. A claim that has never been `live` must not be suspended for a merchant not having
    finished cutover yet."""

    def test_routing_alone_failing_pre_live_never_advances_the_counter(self) -> None:
        result = _advance(
            has_been_live=False,
            ownership_ok=True,
            routing_ok=False,
            certificate_ok=False,
            consecutive_failures=0,
        )
        assert result.status == "failing"
        assert result.consecutive_failures == 0
        assert result.last_failure_counted_on is None

    def test_it_still_never_suspends_no_matter_how_many_days_pass(self) -> None:
        failures = 0
        last_counted: dt.date | None = None
        for day in range(30):
            today = _DAY_ONE + dt.timedelta(days=day)
            result = _advance(
                has_been_live=False,
                ownership_ok=True,
                routing_ok=False,
                certificate_ok=False,
                consecutive_failures=failures,
                last_failure_counted_on=last_counted,
                today=today,
            )
            failures = result.consecutive_failures
            last_counted = result.last_failure_counted_on
            assert result.status == "failing"
        assert failures == 0

    def test_ownership_also_failing_pre_live_does_count(self) -> None:
        """A merchant who stops proving ownership is a different, real problem — routing not
        being finished yet does not exempt a *second*, independent failure from counting."""
        result = _advance(
            has_been_live=False,
            ownership_ok=False,
            routing_ok=False,
            certificate_ok=False,
            consecutive_failures=0,
        )
        assert result.status == "failing"
        assert result.consecutive_failures == 1

    def test_once_it_has_been_live_a_routing_failure_counts_normally(self) -> None:
        result = _advance(
            has_been_live=True,
            ownership_ok=True,
            routing_ok=False,
            certificate_ok=False,
            consecutive_failures=0,
        )
        assert result.status == "failing"
        assert result.consecutive_failures == 1


class TestAuditActionForTransition:
    def test_no_change_is_not_audited(self) -> None:
        assert domain.audit_action_for_transition("live", "live") is None

    @pytest.mark.parametrize(
        ("new_status", "action"),
        [
            ("verified", "storefront.domain.verified"),
            ("live", "storefront.domain.activated"),
            ("failing", "storefront.domain.failing"),
            ("suspended", "storefront.domain.suspended"),
        ],
    )
    def test_every_reachable_transition_has_a_named_action(
        self, new_status: str, action: str
    ) -> None:
        assert domain.audit_action_for_transition("unverified", new_status) == action

    def test_unverified_has_no_action_of_its_own(self) -> None:
        """The claim itself is audited as `storefront.domain.claimed` when it is written, not
        as a transition into a state that existed before anything did."""
        assert domain.audit_action_for_transition("live", "unverified") is None
