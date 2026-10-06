"""Pure rules for rolls: the split's weight apportionment and its exactness, the merge's
agreement checks, and the roll state map. No database."""

from __future__ import annotations

import itertools
import uuid

import pytest

from app import domain
from app.inventory.quantity import format_qty, parse_qty


def q(text: str) -> int:
    return parse_qty(text)


class TestSplitSecondary:
    def test_82_5_metres_into_50_and_32_5_apportions_14_8_kg_exactly(self) -> None:
        shares = domain.split_secondary(q("14.8"), [q("50"), q("32.5")])
        # floor(14800 * 50000 / 82500) = 8969 (8969.69…); the last child takes the remainder.
        assert shares == [8969, 5831]
        assert sum(s for s in shares if s is not None) == q("14.8")
        assert [format_qty(s) for s in shares if s is not None] == ["8.969", "5.831"]

    def test_an_unweighed_roll_gives_unweighed_children(self) -> None:
        assert domain.split_secondary(None, [q("10"), q("5")]) == [None, None]

    @pytest.mark.parametrize(
        ("secondary", "children"),
        [
            (1, [1, 1, 1]),  # one milli-unit over three children: floors give 0, 0; last takes 1
            (14_800, [50_000, 32_500]),
            (999_999_999, [1, 2, 3, 4, 5, 6, 7]),
            (7, [33_333, 33_333, 33_334]),
            (0, [10, 20]),
        ],
    )
    def test_the_weights_always_sum_to_the_parent_exactly(
        self, secondary: int, children: list[int]
    ) -> None:
        shares = domain.split_secondary(secondary, children)
        assert all(s is not None and s >= 0 for s in shares)
        assert sum(s or 0 for s in shares) == secondary

    def test_exactness_holds_over_a_grid_of_awkward_inputs(self) -> None:
        for secondary, parts in itertools.product(
            (1, 13, 14_800, 123_457), ([1, 1], [1, 2, 3], [999, 1], [333, 333, 334], [7] * 9)
        ):
            shares = domain.split_secondary(secondary, parts)
            assert sum(s or 0 for s in shares) == secondary
            # Earlier children never exceed their exact proportional share.
            total = sum(parts)
            for share, part in zip(shares[:-1], parts[:-1], strict=True):
                assert share is not None and share * total <= secondary * part

    def test_a_non_positive_child_is_refused(self) -> None:
        with pytest.raises(domain.RollOperationError):
            domain.split_secondary(10, [5, 0])


class TestValidateSplit:
    def test_the_children_must_account_for_all_of_the_roll(self) -> None:
        domain.validate_split(q("82.5"), [q("50"), q("32.5")])
        with pytest.raises(domain.RollOperationError, match=r"82\.5"):
            domain.validate_split(q("82.5"), [q("50"), q("32.4")])
        with pytest.raises(domain.RollOperationError):
            domain.validate_split(q("82.5"), [q("50"), q("32.6")])

    def test_one_child_is_not_a_split(self) -> None:
        with pytest.raises(domain.RollOperationError):
            domain.validate_split(q("10"), [q("10")])


def _roll(**overrides) -> domain.RollFacts:  # type: ignore[no-untyped-def]
    base = {
        "item_id": uuid.UUID(int=1),
        "lot_id": uuid.UUID(int=2),
        "shade": "NAVY",
        "width_mm": 1500,
        "gsm": 180,
        "grade": "A",
        "secondary_milli": 14_800,
        "secondary_uom": "kg",
    }
    return domain.RollFacts(**{**base, **overrides})


class TestMergeFacts:
    def test_agreeing_rolls_merge_and_weights_add(self) -> None:
        merged = domain.merge_facts([_roll(), _roll(secondary_milli=10_200)])
        assert merged.secondary_milli == 25_000
        assert (merged.width_mm, merged.gsm, merged.grade, merged.shade) == (1500, 180, "A", "NAVY")

    @pytest.mark.parametrize(
        ("override", "message"),
        [
            ({"item_id": uuid.UUID(int=9)}, "different items"),
            ({"lot_id": uuid.UUID(int=9)}, "different lots"),
            ({"lot_id": None}, "different lots"),
            ({"shade": "RED"}, "different shades"),
            ({"shade": None}, "different shades"),
        ],
    )
    def test_item_lot_and_shade_must_all_agree(self, override: dict, message: str) -> None:
        with pytest.raises(domain.RollOperationError, match=message):
            domain.merge_facts([_roll(), _roll(**override)])

    def test_disagreeing_width_gsm_grade_are_cleared_not_invented(self) -> None:
        merged = domain.merge_facts([_roll(), _roll(width_mm=1400, gsm=200, grade="B")])
        assert (merged.width_mm, merged.gsm, merged.grade) == (None, None, None)

    def test_a_weight_is_only_summed_when_every_source_was_weighed_in_one_unit(self) -> None:
        assert domain.merge_facts([_roll(), _roll(secondary_milli=None)]).secondary_milli is None
        mixed = domain.merge_facts([_roll(), _roll(secondary_uom="g")])
        assert (mixed.secondary_milli, mixed.secondary_uom) == (None, None)

    def test_a_merge_needs_two_rolls(self) -> None:
        with pytest.raises(domain.RollOperationError):
            domain.merge_facts([_roll()])


class TestRollTransitions:
    def test_only_an_active_roll_can_be_consumed_and_both_ends_are_terminal(self) -> None:
        assert domain.can_transition_roll("ACTIVE", "SPLIT")
        assert domain.can_transition_roll("ACTIVE", "MERGED")
        for state in ("SPLIT", "MERGED"):
            for target in ("ACTIVE", "SPLIT", "MERGED"):
                assert not domain.can_transition_roll(state, target)
        assert not domain.can_transition_roll("ACTIVE", "ACTIVE")
