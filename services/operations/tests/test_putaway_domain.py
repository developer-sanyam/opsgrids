"""The pure rules behind storage put-away and the warehouse map — no database, no session, exactly
`app.domain`'s own convention. Everything a put-away *decides* before it touches the ledger is
checkable here, by hand, against small inputs whose answer is known in advance:

* `validate_geo` — the table of what a worker device's fix may be, and why it is dropped otherwise;
* `resolve_captured_at` — when the client's claimed time is believed;
* `site_coordinate_errors` — a warehouse's coordinates, the same rule the schema, the PATCH and the
  database CHECKs all state;
* `suggest_putaway_bin` — the "where should this go" rule.

The HTTP behaviour that follows from them is `test_putaway_api.py`'s.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app import domain

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)


# ------------------------------------------------------------------ the vocabulary the owner chose


class TestVocabulary:
    def test_a_put_away_goes_into_a_bin_or_a_shelf_and_nothing_else(self) -> None:
        assert domain.STORABLE_KINDS == ("bin", "shelf")

    def test_a_put_away_starts_from_receiving_or_staging_only(self) -> None:
        """Owner decision D1 (2026-10-05). A put-away from a rack bin is a bin transfer."""
        assert domain.PUTAWAY_SOURCE_KINDS == ("receiving", "staging")

    def test_no_kind_is_both_a_source_and_a_destination(self) -> None:
        assert not set(domain.STORABLE_KINDS) & set(domain.PUTAWAY_SOURCE_KINDS)


# ------------------------------------------------------------------ validate_geo


class TestValidateGeo:
    @pytest.mark.parametrize(
        ("lat", "lng", "acc"),
        [
            (12_971_600, 77_594_600, 8),
            (12_971_600, 77_594_600, None),
            (0, 0, 0),
            (-1, -1, 1),
            (90_000_000, 180_000_000, 0),  # the inclusive upper edges
            (-90_000_000, -180_000_000, 2_147_483_647),  # and the lower, with the widest accuracy
        ],
    )
    def test_a_good_fix_is_kept_as_given(self, lat: int, lng: int, acc: int | None) -> None:
        fix, dropped = domain.validate_geo(lat, lng, acc)
        assert dropped is None
        assert fix == domain.GeoFix(lat_micro=lat, lng_micro=lng, accuracy_m=acc)

    @pytest.mark.parametrize(
        ("lat", "lng", "reason"),
        [
            (12_971_600, None, "unpaired"),
            (None, 77_594_600, "unpaired"),
            (90_000_001, 0, "lat_out_of_range"),
            (-90_000_001, 0, "lat_out_of_range"),
            (0, 180_000_001, "lng_out_of_range"),
            (0, -180_000_001, "lng_out_of_range"),
            # Both wrong: the latitude is reported, deterministically.
            (91_000_000, 181_000_000, "lat_out_of_range"),
            # Far outside what any column holds — still a clean drop, never an overflow.
            (10**30, 0, "lat_out_of_range"),
            (0, -(10**30), "lng_out_of_range"),
            # A latitude that is a longitude's worth of degrees (the classic swapped pair).
            (151_000_000, 33_000_000, "lat_out_of_range"),
        ],
    )
    def test_a_fix_the_server_cannot_use_is_dropped_with_a_reason(
        self, lat: int | None, lng: int | None, reason: str
    ) -> None:
        assert domain.validate_geo(lat, lng, 5) == (None, reason)

    @pytest.mark.parametrize("acc", [-1, -(10**9), 2_147_483_648, 10**30])
    def test_an_accuracy_the_column_cannot_hold_drops_the_whole_fix(self, acc: int) -> None:
        """Not just the accuracy: a fix with an impossible accuracy is not a trustworthy fix."""
        assert domain.validate_geo(12_971_600, 77_594_600, acc) == (None, "accuracy_invalid")

    def test_no_fix_at_all_is_not_a_drop(self) -> None:
        assert domain.validate_geo(None, None, None) == (None, None)

    def test_an_accuracy_with_no_position_is_nothing_to_keep_and_nothing_to_warn_about(
        self,
    ) -> None:
        assert domain.validate_geo(None, None, 12) == (None, None)

    @pytest.mark.parametrize(
        "args", [(1, 2, 3), (None, None, None), (10**40, 10**40, -(10**40)), (None, 1, None)]
    )
    def test_it_never_raises(self, args: tuple[int | None, int | None, int | None]) -> None:
        """A bad fix must not block a stock move, so this has no failure mode of its own."""
        domain.validate_geo(*args)


# ------------------------------------------------------------------ resolve_captured_at


class TestResolveCapturedAt:
    def test_no_claim_is_the_receipt_time_and_not_an_adjustment(self) -> None:
        assert domain.resolve_captured_at(None, NOW) == (NOW, False)

    @pytest.mark.parametrize(
        "claimed",
        [
            NOW,
            NOW - timedelta(seconds=30),
            NOW - timedelta(days=6, hours=23),
            NOW - domain.CAPTURE_MAX_AGE,  # exactly seven days: still believed
            NOW + timedelta(minutes=4),  # a device clock a little ahead
            NOW + domain.CAPTURE_MAX_FUTURE,  # exactly five minutes ahead: still believed
        ],
    )
    def test_a_plausible_claim_is_kept(self, claimed: datetime) -> None:
        assert domain.resolve_captured_at(claimed, NOW) == (claimed, False)

    @pytest.mark.parametrize(
        "claimed",
        [
            NOW + domain.CAPTURE_MAX_FUTURE + timedelta(seconds=1),
            NOW + timedelta(hours=1),
            NOW + timedelta(days=400),
            NOW - domain.CAPTURE_MAX_AGE - timedelta(seconds=1),
            NOW - timedelta(days=30),
            datetime(1970, 1, 1, tzinfo=UTC),
        ],
    )
    def test_an_absurd_claim_is_replaced_by_the_receipt_time_and_flagged(
        self, claimed: datetime
    ) -> None:
        assert domain.resolve_captured_at(claimed, NOW) == (NOW, True)

    def test_a_claim_with_no_zone_names_no_instant_and_is_replaced(self) -> None:
        naive = datetime(2026, 10, 5, 11, 59, 0)
        assert domain.resolve_captured_at(naive, NOW) == (NOW, True)

    def test_another_zone_is_compared_as_an_instant_and_kept_in_its_own_zone(self) -> None:
        ist = timezone(timedelta(hours=5, minutes=30))
        claimed = datetime(2026, 10, 5, 17, 25, 0, tzinfo=ist)  # 11:55 UTC, five minutes ago
        assert domain.resolve_captured_at(claimed, NOW) == (claimed, False)

    def test_a_wall_clock_that_looks_recent_but_is_a_day_ahead_in_utc_is_replaced(self) -> None:
        # 12:00 at UTC-14 is 02:00 UTC the next day: fourteen hours in the future.
        ahead = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone(timedelta(hours=-14)))
        assert domain.resolve_captured_at(ahead, NOW) == (NOW, True)


# ------------------------------------------------------------------ site_coordinate_errors


class TestSiteCoordinateErrors:
    @pytest.mark.parametrize(
        ("lat", "lng"),
        [
            (None, None),
            (0, 0),
            (12_971_600, 77_594_600),
            (90_000_000, 180_000_000),
            (-90_000_000, -180_000_000),
        ],
    )
    def test_both_null_or_both_set_in_range_is_fine(self, lat: int | None, lng: int | None) -> None:
        assert domain.site_coordinate_errors(lat, lng) == []

    @pytest.mark.parametrize(
        ("lat", "lng", "field"),
        [
            (12_971_600, None, "lng_micro"),
            (None, 77_594_600, "lat_micro"),
        ],
    )
    def test_a_lone_half_names_the_missing_one(
        self, lat: int | None, lng: int | None, field: str
    ) -> None:
        errors = domain.site_coordinate_errors(lat, lng)
        assert [f for f, _ in errors] == [field]

    @pytest.mark.parametrize(
        ("lat", "lng", "fields"),
        [
            (90_000_001, 0, ["lat_micro"]),
            (-90_000_001, 0, ["lat_micro"]),
            (0, 180_000_001, ["lng_micro"]),
            (0, -180_000_001, ["lng_micro"]),
            (95_000_000, 190_000_000, ["lat_micro", "lng_micro"]),
        ],
    )
    def test_out_of_range_names_each_field_that_is_out(
        self, lat: int, lng: int, fields: list[str]
    ) -> None:
        assert [f for f, _ in domain.site_coordinate_errors(lat, lng)] == fields


# ------------------------------------------------------------------ suggest_putaway_bin


def _bin(
    code: str,
    *,
    seq: int | None = None,
    kind: str = "bin",
    holds: bool = False,
    empty: bool = True,
) -> domain.PutawayBin:
    return domain.PutawayBin(
        location_id=uuid.uuid5(uuid.NAMESPACE_URL, code),
        code=code,
        kind=kind,
        pick_sequence=seq,
        holds_item=holds,
        is_empty=empty and not holds,
    )


def _id(code: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, code)


class TestSuggestPutawayBin:
    def test_nothing_to_choose_from_is_no_suggestion(self) -> None:
        assert domain.suggest_putaway_bin([]) is None

    def test_a_bin_already_holding_the_item_wins_over_an_emptier_walk_position(self) -> None:
        bins = [_bin("A-01", seq=1), _bin("Z-09", seq=99, holds=True)]
        assert domain.suggest_putaway_bin(bins) == domain.PutawaySuggestion(
            _id("Z-09"), "holds_item"
        )

    def test_among_bins_holding_the_item_the_first_in_walk_order_wins(self) -> None:
        bins = [
            _bin("C", seq=3, holds=True),
            _bin("B", seq=2, holds=True),
            _bin("A", seq=None, holds=True),  # no pick sequence sorts last
        ]
        assert domain.suggest_putaway_bin(bins) == domain.PutawaySuggestion(_id("B"), "holds_item")

    def test_with_none_holding_it_the_first_empty_bin_in_walk_order_wins(self) -> None:
        bins = [
            _bin("B-02", seq=2),
            _bin("B-01", seq=2),
            _bin("C-01", seq=None),
            _bin("A-01", seq=5),
        ]
        # Lowest sequence 2; the tie is broken by code.
        assert domain.suggest_putaway_bin(bins) == domain.PutawaySuggestion(
            _id("B-01"), "empty_bin"
        )

    def test_a_bin_with_no_sequence_is_chosen_only_when_every_other_is_unavailable(self) -> None:
        assert domain.suggest_putaway_bin([_bin("ONLY")]) == domain.PutawaySuggestion(
            _id("ONLY"), "empty_bin"
        )

    def test_a_bin_holding_other_stock_is_neither_holding_this_item_nor_empty(self) -> None:
        busy = _bin("BUSY", seq=1, holds=False, empty=False)
        assert domain.suggest_putaway_bin([busy]) is None

    def test_only_a_bin_or_a_shelf_is_ever_suggested(self) -> None:
        bins = [
            _bin("ZONE", seq=1, kind="zone", holds=True),
            _bin("RACK", seq=1, kind="rack"),
            _bin("STG", seq=1, kind="staging"),
            _bin("RCV", seq=1, kind="receiving"),
            _bin("QUAR", seq=1, kind="quarantine"),
            _bin("SHELF", seq=7, kind="shelf"),
        ]
        assert domain.suggest_putaway_bin(bins) == domain.PutawaySuggestion(
            _id("SHELF"), "empty_bin"
        )

    def test_no_storable_bin_at_all_is_no_suggestion(self) -> None:
        assert (
            domain.suggest_putaway_bin([_bin("RACK", kind="rack"), _bin("Z", kind="zone")]) is None
        )

    def test_the_answer_does_not_depend_on_the_order_the_bins_arrive_in(self) -> None:
        bins = [_bin(f"B-{i:02d}", seq=i % 4) for i in range(12)]
        expected = domain.suggest_putaway_bin(bins)
        assert domain.suggest_putaway_bin(list(reversed(bins))) == expected
        assert domain.suggest_putaway_bin(bins[5:] + bins[:5]) == expected
