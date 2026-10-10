"""Pure manufacturing rules: no database, no HTTP. The arithmetic here is what every production
order's figures come from, so it is pinned with hand-worked numbers and with properties (no
rounding drift when output is split, never plan less than is needed)."""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest

from app.manufacturing import domain


class TestStateMachines:
    def test_production_order_map_is_exactly_the_documented_flow(self) -> None:
        t = domain.PRODUCTION_ORDER_TRANSITIONS
        assert t["DRAFT"] == {"APPROVED", "CANCELLED"}
        assert t["APPROVED"] == {"IN_PROGRESS", "CANCELLED"}
        assert t["IN_PROGRESS"] == {"COMPLETED", "CANCELLED"}
        assert t["COMPLETED"] == frozenset() and t["CANCELLED"] == frozenset()
        assert set(t) == set(domain.PRODUCTION_ORDER_STATUSES)

    @pytest.mark.parametrize("status", domain.PRODUCTION_ORDER_STATUSES)
    def test_a_no_op_move_is_refused(self, status: str) -> None:
        assert not domain.can_transition_production_order(status, status)

    def test_nothing_leaves_a_terminal_state(self) -> None:
        for terminal in ("COMPLETED", "CANCELLED"):
            for target in domain.PRODUCTION_ORDER_STATUSES:
                assert not domain.can_transition_production_order(terminal, target)

    def test_a_bom_version_only_goes_forward(self) -> None:
        assert domain.can_transition_bom_version("DRAFT", "PUBLISHED")
        assert domain.can_transition_bom_version("PUBLISHED", "RETIRED")
        assert not domain.can_transition_bom_version("PUBLISHED", "DRAFT")
        assert not domain.can_transition_bom_version("RETIRED", "PUBLISHED")
        assert not domain.can_transition_bom_version("DRAFT", "RETIRED")


class TestPercentages:
    @pytest.mark.parametrize(
        ("text", "bps"),
        [("0", 0), ("2.5", 250), ("2.50", 250), ("0.01", 1), ("100", 10_000), ("12.34", 1234)],
    )
    def test_parse(self, text: str, bps: int) -> None:
        assert domain.parse_pct_bps(text) == bps

    @pytest.mark.parametrize("bad", ["", "-1", "1.234", "abc", "100.01", "1e2", " "])
    def test_refuses(self, bad: str) -> None:
        with pytest.raises(domain.PercentError):
            domain.parse_pct_bps(bad)

    def test_never_a_float(self) -> None:
        with pytest.raises(domain.PercentError):
            domain.parse_pct_bps(2.5)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bps", [0, 1, 250, 1234, 10_000])
    def test_round_trips(self, bps: int) -> None:
        assert domain.parse_pct_bps(domain.format_bps(bps)) == bps


class TestRequirements:
    def test_scrap_is_applied_once_and_rounded_up(self) -> None:
        # 1.234 m per piece, 7 pieces, 2.5 % scrap: 8.85395 m -> 8.854
        assert (
            domain.required_milli(
                component_milli=1234, output_milli=1000, planned_milli=7000, scrap_bps=250
            )
            == 8854
        )

    def test_a_batch_bom_scales_down(self) -> None:
        # 5 kg per 100 pieces; 30 pieces need 1.5 kg
        assert (
            domain.required_milli(component_milli=5000, output_milli=100_000, planned_milli=30_000)
            == 1500
        )

    def test_a_discrete_unit_is_rounded_up_to_whole_units(self) -> None:
        # 1 button per 3 pieces; 10 pieces = 3.333 -> 4 whole buttons
        assert (
            domain.required_milli(
                component_milli=1000,
                output_milli=3000,
                planned_milli=10_000,
                discrete=True,
            )
            == 4000
        )

    def test_a_substitute_ratio_scales_the_need(self) -> None:
        assert (
            domain.required_milli(
                component_milli=2000, output_milli=1000, planned_milli=3000, ratio_milli=1250
            )
            == 7500
        )

    def test_never_plans_less_than_needed(self) -> None:
        for planned in range(1, 400):
            exact_num = 1234 * planned * 10250 * 1000
            exact_den = 1000 * 10_000 * 1000
            got = domain.required_milli(
                component_milli=1234, output_milli=1000, planned_milli=planned, scrap_bps=250
            )
            assert got * exact_den >= exact_num
            assert (got - 1) * exact_den < exact_num

    def test_splitting_output_never_drifts(self) -> None:
        """Consumption is cumulative: the sum of the parts is the whole, whatever the split."""
        whole = domain.theoretical_milli(component_milli=1234, output_milli=1000, good_milli=7000)
        consumed = 0
        total = 0
        for part in (1000, 2000, 1000, 3000):
            total += part
            need = domain.theoretical_milli(
                component_milli=1234, output_milli=1000, good_milli=total
            )
            consumed += need - consumed
        assert consumed == whole == 8638

    def test_a_denominator_is_never_zero(self) -> None:
        with pytest.raises(ValueError):
            domain.required_milli(component_milli=1, output_milli=0, planned_milli=1)

    def test_allowance(self) -> None:
        # 5 % of 20 m = 1 m ; 0.01 % of 1 m rounds up to a milli
        assert domain.allowance_milli(20_000, 500) == 1000
        assert domain.allowance_milli(1000, 1) == 1
        assert domain.allowance_milli(1000, 0) == 0


class TestThresholdChoice:
    ROWS: ClassVar[list[tuple[str | None, str | None, int]]] = [
        (None, None, 800),  # everything
        (None, "DEFECT", 300),  # defects anywhere
        ("shirt", None, 500),  # this product, any type
        ("shirt", "DEFECT", 100),  # this product's defects
    ]

    def test_the_most_specific_row_wins(self) -> None:
        pick = domain.pick_threshold
        assert pick(self.ROWS, item_id="shirt", wastage_type="DEFECT") == ("DEFECT", 100)
        assert pick(self.ROWS, item_id="shirt", wastage_type="CUTTING") == (None, 500)
        assert pick(self.ROWS, item_id="coat", wastage_type="DEFECT") == ("DEFECT", 300)
        assert pick(self.ROWS, item_id="coat", wastage_type="CUTTING") == (None, 800)

    def test_none_when_nothing_matches(self) -> None:
        assert (
            domain.pick_threshold([("coat", None, 100)], item_id="shirt", wastage_type="X") is None
        )


class TestFifo:
    def test_takes_oldest_first_and_reports_the_shortfall(self) -> None:
        draws, short = domain.allocate_fifo([("a", 5), ("b", 5), ("c", 5)], 7)
        assert draws == [("a", 5), ("b", 2)] and short == 0
        draws, short = domain.allocate_fifo([("a", 5), ("b", 0)], 9)
        assert draws == [("a", 5)] and short == 4

    def test_zero_need_takes_nothing(self) -> None:
        assert domain.allocate_fifo([("a", 5)], 0) == ([], 0)


class TestEffectiveDates:
    V1 = domain.VersionFacts("v1", 1, None, None, "PUBLISHED")
    V2 = domain.VersionFacts("v2", 2, date(2026, 11, 1), None, "PUBLISHED")
    V3 = domain.VersionFacts("v3", 3, date(2027, 1, 1), date(2027, 1, 31), "PUBLISHED")

    def test_a_later_version_supersedes_from_its_own_start_date(self) -> None:
        versions = [self.V1, self.V2, self.V3]
        assert domain.resolve_version(versions, date(2026, 10, 31)).id == "v1"  # type: ignore[union-attr]
        assert domain.resolve_version(versions, date(2026, 11, 1)).id == "v2"  # type: ignore[union-attr]
        assert domain.resolve_version(versions, date(2027, 1, 15)).id == "v3"  # type: ignore[union-attr]
        # v3 expired: v2 (open-ended) is back in force.
        assert domain.resolve_version(versions, date(2027, 2, 1)).id == "v2"  # type: ignore[union-attr]

    def test_drafts_and_retired_versions_are_never_chosen(self) -> None:
        draft = domain.VersionFacts("d", 9, None, None, "DRAFT")
        retired = domain.VersionFacts("r", 8, None, None, "RETIRED")
        assert domain.resolve_version([draft, retired], date(2026, 1, 1)) is None

    def test_a_window_with_nothing_in_force_is_none(self) -> None:
        late = domain.VersionFacts("v", 1, date(2030, 1, 1), None, "PUBLISHED")
        assert domain.resolve_version([late], date(2026, 1, 1)) is None


class TestCycles:
    def test_direct_self_reference(self) -> None:
        assert domain.find_cycle("a", {"a": {"a"}}) == ["a", "a"]

    def test_indirect_loop_names_the_path(self) -> None:
        path = domain.find_cycle("a", {"a": {"b"}, "b": {"c"}, "c": {"a"}})
        assert path is not None and path[0] == path[-1] == "a" and set(path) == {"a", "b", "c"}

    def test_a_diamond_is_not_a_cycle(self) -> None:
        edges = {"a": {"b", "c"}, "b": {"d"}, "c": {"d"}}
        assert domain.find_cycle("a", edges) is None

    def test_a_loop_not_through_the_root_is_not_reported_for_it(self) -> None:
        assert domain.find_cycle("a", {"a": {"b"}, "b": {"c"}, "c": {"b"}}) is None

    def test_deep_chains_do_not_exhaust_the_stack(self) -> None:
        edges = {str(i): {str(i + 1)} for i in range(5000)}
        assert domain.find_cycle("0", edges) is None
        edges["5000"] = {"0"}
        assert domain.find_cycle("0", edges) is not None


class TestExplosion:
    BOMS: ClassVar[dict[str, domain.ExplodeBom]] = {
        # 1 shirt = 2 panels (10 % scrap) + 6 buttons
        "shirt": domain.ExplodeBom(
            "shirt",
            1000,
            (
                domain.ExplodeLine("panel", 2000, 1000),
                domain.ExplodeLine("button", 6000, 0, discrete=True),
            ),
        ),
        # 10 m of panel = 12 m of fabric
        "panel": domain.ExplodeBom("panel", 10_000, (domain.ExplodeLine("fabric", 12_000, 0),)),
    }

    def test_multi_level_requirements_carry_down(self) -> None:
        tree = domain.explode("shirt", 5000, self.BOMS.get)
        by_item = {c.item_id: c for c in tree.children}
        assert by_item["panel"].quantity_milli == 11_000  # 5 * 2 * 1.1
        assert by_item["button"].quantity_milli == 30_000
        (fabric,) = by_item["panel"].children
        assert fabric.item_id == "fabric" and fabric.quantity_milli == 13_200  # 11 * 12 / 10
        assert not fabric.is_produced and by_item["panel"].is_produced
        assert fabric.level == 2

    def test_a_cycle_is_refused_not_looped_on(self) -> None:
        loop = {
            "a": domain.ExplodeBom("a", 1000, (domain.ExplodeLine("b", 1000),)),
            "b": domain.ExplodeBom("b", 1000, (domain.ExplodeLine("a", 1000),)),
        }
        with pytest.raises(ValueError, match="cycle"):
            domain.explode("a", 1000, loop.get)
