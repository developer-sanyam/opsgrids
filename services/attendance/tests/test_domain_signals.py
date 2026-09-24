"""The anti-spoofing rules' pure functions (R22): distance, travel plausibility, address parsing,
range matching and user-agent classification. No database — everything here is arithmetic and
string handling, so it is asserted exhaustively rather than through a punch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain import (
    MIN_TRAVEL_DISTANCE_M,
    UserAgentClass,
    assess_travel,
    classify_user_agent,
    haversine_m,
    ip_in_any,
    normalise_cidr,
    parse_ip,
)

T0 = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)

MUMBAI = (19.0760, 72.8777)
DELHI = (28.6139, 77.2090)


class TestHaversine:
    def test_the_same_point_is_zero(self) -> None:
        assert haversine_m(*MUMBAI, *MUMBAI) == 0.0

    def test_mumbai_to_delhi_is_about_1150_km(self) -> None:
        assert haversine_m(*MUMBAI, *DELHI) == pytest.approx(1_153_000, rel=0.01)

    def test_it_is_symmetric(self) -> None:
        assert haversine_m(*MUMBAI, *DELHI) == pytest.approx(haversine_m(*DELHI, *MUMBAI))

    def test_a_degree_of_latitude_is_about_111_km(self) -> None:
        assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(111_195, rel=0.001)

    def test_antipodal_points_do_not_raise_a_domain_error(self) -> None:
        """Rounding can push the haversine term a hair past 1.0; `asin` of that is a domain error
        unless it is clamped. Half the planet is the answer, not an exception."""
        assert haversine_m(0.0, 0.0, 0.0, 180.0) == pytest.approx(20_015_087, rel=0.001)
        assert haversine_m(90.0, 0.0, -90.0, 0.0) == pytest.approx(20_015_087, rel=0.001)

    def test_it_crosses_the_antimeridian_the_short_way(self) -> None:
        assert haversine_m(0.0, 179.5, 0.0, -179.5) == pytest.approx(111_195, rel=0.001)


def _travel(
    *,
    dist_lat: float = 0.0,
    elapsed: timedelta = timedelta(minutes=1),
    from_accuracy: float | None = None,
    to_accuracy: float | None = None,
    max_speed: float = 900.0,
):
    """Two punches on the same meridian, `dist_lat` degrees of latitude apart."""
    return assess_travel(
        from_lat=19.0,
        from_lng=72.0,
        from_accuracy_m=from_accuracy,
        from_at=T0,
        to_lat=19.0 + dist_lat,
        to_lng=72.0,
        to_accuracy_m=to_accuracy,
        to_at=T0 + elapsed,
        max_speed_kmh=max_speed,
    )


class TestAssessTravel:
    def test_a_flight_in_a_minute_is_impossible(self) -> None:
        verdict = assess_travel(
            from_lat=MUMBAI[0],
            from_lng=MUMBAI[1],
            from_accuracy_m=10,
            from_at=T0,
            to_lat=DELHI[0],
            to_lng=DELHI[1],
            to_accuracy_m=10,
            to_at=T0 + timedelta(minutes=1),
            max_speed_kmh=900,
        )
        assert verdict.impossible
        assert verdict.speed_kmh > 60_000

    def test_the_same_trip_over_a_day_is_fine(self) -> None:
        verdict = assess_travel(
            from_lat=MUMBAI[0],
            from_lng=MUMBAI[1],
            from_accuracy_m=None,
            from_at=T0,
            to_lat=DELHI[0],
            to_lng=DELHI[1],
            to_accuracy_m=None,
            to_at=T0 + timedelta(hours=24),
            max_speed_kmh=900,
        )
        assert not verdict.impossible
        assert verdict.speed_kmh == pytest.approx(48, rel=0.02)

    def test_the_speed_threshold_is_the_organizations(self) -> None:
        """Five km in one minute is 300 km/h: impossible at a 100 km/h ceiling, plausible at 400."""
        assert _travel(dist_lat=0.045, max_speed=100.0).impossible
        assert not _travel(dist_lat=0.045, max_speed=400.0).impossible

    def test_accuracy_is_credited_to_the_person_not_the_accuser(self) -> None:
        """ "Somewhere within 2 km" twice is not evidence of covering the gap between the guesses:
        both radii come off the distance before it becomes a speed."""
        without = _travel(dist_lat=0.045, max_speed=100.0)
        with_credit = _travel(
            dist_lat=0.045, max_speed=100.0, from_accuracy=2_000, to_accuracy=2_000
        )
        assert without.impossible
        assert with_credit.effective_m < without.effective_m
        assert with_credit.effective_m == pytest.approx(
            max(0.0, without.distance_m - 4_000), abs=1e-6
        )
        assert not with_credit.impossible

    def test_more_accuracy_than_distance_is_zero_not_negative(self) -> None:
        verdict = _travel(dist_lat=0.001, from_accuracy=5_000, to_accuracy=5_000)
        assert verdict.effective_m == 0.0
        assert verdict.speed_kmh == 0.0
        assert not verdict.impossible

    def test_a_missing_accuracy_gives_no_credit(self) -> None:
        assert _travel(dist_lat=0.045).effective_m == pytest.approx(
            _travel(dist_lat=0.045).distance_m
        )

    def test_gps_jitter_between_two_close_fixes_is_never_travel(self) -> None:
        """Two fixes a few hundred metres apart one second apart would be thousands of km/h — and
        are what an ordinary phone produces indoors. Nothing under the floor is judged."""
        verdict = _travel(dist_lat=0.004, elapsed=timedelta(seconds=1))
        assert verdict.distance_m < MIN_TRAVEL_DISTANCE_M
        assert verdict.speed_kmh > 900
        assert not verdict.impossible

    def test_two_punches_stamped_in_the_same_instant_do_not_divide_by_zero(self) -> None:
        verdict = _travel(dist_lat=1.0, elapsed=timedelta(0))
        assert verdict.elapsed_s == 1.0
        assert verdict.impossible

    def test_a_clock_that_ran_backwards_is_treated_as_one_second(self) -> None:
        verdict = _travel(dist_lat=1.0, elapsed=timedelta(minutes=-5))
        assert verdict.elapsed_s == 1.0
        assert verdict.impossible

    def test_the_speed_ceiling_itself_is_not_impossible(self) -> None:
        """Strictly above the ceiling is impossible; exactly at it is not."""
        # 900 km in exactly one hour, on a meridian: 900 / 111.195 degrees.
        verdict = _travel(dist_lat=900 / 111.19508, elapsed=timedelta(hours=1))
        assert verdict.speed_kmh == pytest.approx(900, rel=0.001)


class TestParseIp:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("203.0.113.9", "203.0.113.9"),
            ("  203.0.113.9  ", "203.0.113.9"),
            ("2001:db8::1", "2001:db8::1"),
            ("2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
        ],
    )
    def test_it_normalises_an_address(self, raw: str, expected: str) -> None:
        assert parse_ip(raw) == expected

    def test_an_ipv4_client_on_a_dual_stack_listener_is_read_as_ipv4(self) -> None:
        """`::ffff:203.0.113.9` is the same host as `203.0.113.9`. Left as IPv6 it would match no
        IPv4 range, and every office punch from a dual-stack socket would read as off-network."""
        assert parse_ip("::ffff:203.0.113.9") == "203.0.113.9"
        assert ip_in_any(parse_ip("::ffff:203.0.113.9") or "", ["203.0.113.0/24"])

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "   ",
            "unknown",
            "localhost",
            "203.0.113.9:443",
            "999.1.1.1",
            "1.2.3",
            "<script>",
        ],
    )
    def test_anything_else_is_no_address_at_all(self, raw: str | None) -> None:
        """A forwarded header is text somebody typed. What is not an address is left out, never
        stored, never matched."""
        assert parse_ip(raw) is None


class TestCidr:
    def test_a_range_is_canonicalised(self) -> None:
        assert normalise_cidr("203.0.113.0/24") == "203.0.113.0/24"
        assert normalise_cidr(" 2001:DB8::/32 ") == "2001:db8::/32"

    def test_a_host_written_with_a_mask_is_read_as_the_network_it_lives_in(self) -> None:
        assert normalise_cidr("203.0.113.77/24") == "203.0.113.0/24"

    def test_a_bare_address_is_a_single_host_range(self) -> None:
        assert normalise_cidr("203.0.113.9") == "203.0.113.9/32"

    @pytest.mark.parametrize("raw", ["0.0.0.0/0", "::/0"])
    def test_a_range_covering_every_address_is_refused(self, raw: str) -> None:
        with pytest.raises(ValueError, match="every address"):
            normalise_cidr(raw)

    @pytest.mark.parametrize("raw", ["", "nonsense", "203.0.113.0/33", "203.0.113.0/24/8"])
    def test_garbage_is_refused(self, raw: str) -> None:
        with pytest.raises(ValueError):
            normalise_cidr(raw)


class TestIpInAny:
    def test_inside_and_outside(self) -> None:
        ranges = ["203.0.113.0/24", "198.51.100.0/25"]
        assert ip_in_any("203.0.113.200", ranges)
        assert ip_in_any("198.51.100.5", ranges)
        assert not ip_in_any("198.51.100.200", ranges)
        assert not ip_in_any("192.0.2.1", ranges)

    def test_the_boundaries_of_a_range(self) -> None:
        assert ip_in_any("203.0.113.0", ["203.0.113.0/24"])
        assert ip_in_any("203.0.113.255", ["203.0.113.0/24"])
        assert not ip_in_any("203.0.114.0", ["203.0.113.0/24"])

    def test_an_empty_list_matches_nothing(self) -> None:
        assert not ip_in_any("203.0.113.9", [])

    def test_the_two_address_families_never_match_each_other(self) -> None:
        assert not ip_in_any("203.0.113.9", ["2001:db8::/32"])
        assert not ip_in_any("2001:db8::1", ["203.0.113.0/24"])
        assert ip_in_any("2001:db8::1", ["2001:db8::/32"])


class TestClassifyUserAgent:
    @pytest.mark.parametrize(
        ("ua", "expected"),
        [
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                UserAgentClass.BROWSER,
            ),
            (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15",
                UserAgentClass.BROWSER,
            ),
            ("okhttp/4.12.0", UserAgentClass.MOBILE_APP),
            ("OpsGrids-Android/1.4.2 (Pixel 8)", UserAgentClass.MOBILE_APP),
            ("MyApp/12 CFNetwork/1494.0.7 Darwin/23.4.0", UserAgentClass.MOBILE_APP),
            ("curl/8.4.0", UserAgentClass.AUTOMATION),
            ("python-httpx/0.27.0", UserAgentClass.AUTOMATION),
            ("python-requests/2.32.0", UserAgentClass.AUTOMATION),
            ("PostmanRuntime/7.39.0", UserAgentClass.AUTOMATION),
            (None, UserAgentClass.UNKNOWN),
            ("", UserAgentClass.UNKNOWN),
            ("SomethingElse/1.0", UserAgentClass.UNKNOWN),
        ],
    )
    def test_the_class(self, ua: str | None, expected: UserAgentClass) -> None:
        assert classify_user_agent(ua) is expected

    def test_a_headless_browser_is_automation_not_a_person(self) -> None:
        """It says `Mozilla` like any browser; scripted markers are checked first."""
        ua = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "HeadlessChrome/126.0.0.0 Safari/537.36"
        )
        assert classify_user_agent(ua) is UserAgentClass.AUTOMATION

    def test_matching_is_case_insensitive(self) -> None:
        assert classify_user_agent("CURL/8.4.0") is UserAgentClass.AUTOMATION
