"""Pure MRP arithmetic: explosion, netting, pegging, cycles, determinism. No database."""

from __future__ import annotations

import random
from datetime import date

import pytest

from app.mrp import domain
from app.mrp.domain import MrpBom, MrpCycleError, MrpLine, Peg, Supply, allocate_proportional, plan

SHIRT, PANEL, FABRIC, DYE, BUTTON = "shirt", "panel", "fabric", "dye", "button"

BOMS: dict[str, MrpBom | None] = {
    # 1 shirt = 1.2 m of panel + 5 buttons (discrete)
    SHIRT: MrpBom(
        SHIRT,
        1000,
        (
            MrpLine(PANEL, 1200),
            MrpLine(BUTTON, 5000, discrete=True),
        ),
    ),
    # 1 m of panel = 1.234 m fabric at 2.5 % scrap + 0.05 kg dye at 10 % scrap
    PANEL: MrpBom(
        PANEL, 1000, (MrpLine(FABRIC, 1234, scrap_bps=250), MrpLine(DYE, 50, scrap_bps=1000))
    ),
}


def run(demands: list[tuple[str, Peg]], supplies: dict[str, Supply] | None = None, boms=BOMS):
    supplies = supplies or {}
    return plan(
        [p for _, p in demands],
        [i for i, _ in demands],
        lambda i: supplies.get(i, Supply()),
        lambda i: boms.get(i),
    )


def by_item(result: domain.PlanResult) -> dict[str, domain.ItemPlan]:
    return {i.item_id: i for i in result.items}


def demand(item: str, qty: int, ref: str = "SO-1", due: date | None = None) -> tuple[str, Peg]:
    return item, Peg("SALES_ORDER", f"id-{ref}", ref, qty, due)


def test_multi_level_explosion_with_scrap_is_exact() -> None:
    result = by_item(run([demand(SHIRT, 10_000)]))
    # 10 shirts -> 12 m panel, 50 buttons; 12 m panel -> 1.234 * 12 * 1.025 = 15.1782 m fabric
    assert result[SHIRT].net_milli == 10_000 and result[SHIRT].action == "MAKE"
    assert result[PANEL].gross_milli == 12_000 and result[PANEL].level == 1
    assert result[BUTTON].gross_milli == 50_000
    assert result[FABRIC].gross_milli == 15_179  # ceil(15178.2): rounded up once
    assert result[DYE].gross_milli == 660  # 0.05 * 12 * 1.10 = 0.66 kg
    assert result[FABRIC].action == "BUY" and result[FABRIC].level == 2


def test_each_levels_need_is_rounded_up_once() -> None:
    # 7 pieces: panel 8.4 m exactly; fabric 8.4 * 1.234 * 1.025 = 10.6250..., ceil'd once
    result = by_item(run([demand(SHIRT, 7_000)]))
    assert result[PANEL].gross_milli == 8_400
    expected = -(-1234 * 8400 * 10250 * 1000 // (1000 * 10000 * 1000))
    assert result[FABRIC].gross_milli == expected == 10_625


def test_netting_uses_available_reserved_po_and_open_production() -> None:
    supplies = {
        # panel: 4 on hand, 1 reserved elsewhere -> 3 free; 2 from an open PO; 1.5 coming from
        # an open production order -> 6.5 of supply against 12
        PANEL: Supply(
            on_hand_milli=4_000,
            reserved_milli=1_000,
            open_po_milli=2_000,
            open_production_milli=1_500,
        ),
        FABRIC: Supply(on_hand_milli=20_000),
        BUTTON: Supply(on_hand_milli=10_000, open_po_milli=15_000),
    }
    result = by_item(run([demand(SHIRT, 10_000)], supplies))
    panel = result[PANEL]
    assert panel.supply.total_milli == 6_500
    assert panel.net_milli == 5_500 and panel.action == "MAKE"
    # only the *net* panel explodes: 5.5 m -> fabric 5.5 * 1.234 * 1.025 = 6.9570... -> 6957
    fabric = result[FABRIC]
    assert fabric.gross_milli == -(-1234 * 5500 * 10250 * 1000 // (1000 * 10000 * 1000))
    assert fabric.net_milli == 0 and fabric.action is None  # 20 m on hand covers it
    button = result[BUTTON]
    assert (button.gross_milli, button.net_milli) == (50_000, 25_000) and button.action == "BUY"
    assert {s.item_id for s in run([demand(SHIRT, 10_000)], supplies).shortages} == {
        SHIRT,
        PANEL,
        BUTTON,
        DYE,
    }


def test_stock_of_a_subassembly_stops_the_explosion() -> None:
    result = by_item(run([demand(SHIRT, 10_000)], {PANEL: Supply(on_hand_milli=12_000)}))
    assert result[PANEL].net_milli == 0
    assert FABRIC not in result and DYE not in result  # nothing below a covered panel


def test_reserved_cannot_exceed_on_hand_in_supply() -> None:
    assert Supply(on_hand_milli=3_000, reserved_milli=5_000).free_milli == 0


def test_a_shared_component_is_netted_once_against_all_demand() -> None:
    # fabric is wanted by two panels' worth of demand and directly: one pool, one shortage
    result = by_item(
        run(
            [
                demand(SHIRT, 5_000, "SO-1"),
                demand(PANEL, 2_000, "SO-2"),
                demand(FABRIC, 1_000, "SO-3"),
            ],
            {FABRIC: Supply(on_hand_milli=5_000)},
        )
    )
    # panel gross = 6 + 2 = 8; fabric gross = 8*1.234*1.025 + direct 1
    panel_fabric = -(-1234 * 8000 * 10250 * 1000 // (1000 * 10000 * 1000))
    assert result[FABRIC].gross_milli == panel_fabric + 1_000
    assert result[FABRIC].net_milli == panel_fabric + 1_000 - 5_000
    # processed after both parents: level is the longest path
    assert result[FABRIC].level == 2


def test_pegging_traces_a_component_shortage_to_the_root_demand() -> None:
    result = by_item(
        run(
            [
                demand(SHIRT, 4_000, "SO-A", date(2026, 11, 1)),
                demand(SHIRT, 6_000, "SO-B", date(2026, 12, 1)),
            ],
            {SHIRT: Supply(on_hand_milli=4_000)},  # covers the earliest (SO-A) first
        )
    )
    shirt = result[SHIRT]
    assert shirt.net_milli == 6_000 and shirt.need_by == date(2026, 12, 1)
    button = result[BUTTON]
    assert button.gross_milli == 30_000
    # every button traces to SO-B through the shirt, none to SO-A
    assert {(p.source_ref, p.parent_item_id, p.quantity_milli) for p in button.pegs} == {
        ("SO-B", SHIRT, 30_000)
    }
    assert button.need_by == date(2026, 12, 1)
    assert sum(p.quantity_milli for p in result[FABRIC].pegs) == result[FABRIC].gross_milli


def test_peg_shares_always_sum_to_the_need() -> None:
    rnd = random.Random(7)  # noqa: S311 - a seeded fixture, not a secret
    for _ in range(300):
        weights = [rnd.randint(0, 50_000) for _ in range(rnd.randint(1, 9))]
        total = rnd.randint(0, 900_000)
        shares = allocate_proportional(total, weights)
        assert len(shares) == len(weights)
        if sum(weights) > 0:
            assert sum(shares) == total
        else:
            assert sum(shares) == 0
        assert all(s >= 0 for s in shares)
        # nobody is allocated more than their proportional share rounded up
        for s, w in zip(shares, weights, strict=True):
            if sum(weights):
                assert s <= -(-total * w // sum(weights))


def test_a_discrete_component_is_rounded_to_whole_units() -> None:
    boms = {
        SHIRT: MrpBom(SHIRT, 1000, (MrpLine(BUTTON, 2500, discrete=True),)),  # 2.5 buttons / shirt
    }
    assert by_item(run([demand(SHIRT, 3_000)], boms=boms))[BUTTON].gross_milli == 8_000  # 7.5 -> 8


def test_a_chosen_substitute_scales_by_its_ratio() -> None:
    boms = {SHIRT: MrpBom(SHIRT, 1000, (MrpLine("alt-fabric", 1000, ratio_milli=1250),))}
    assert by_item(run([demand(SHIRT, 2_000)], boms=boms))["alt-fabric"].gross_milli == 2_500


def test_batch_size_divides_the_line() -> None:
    boms = {SHIRT: MrpBom(SHIRT, 100_000, (MrpLine(FABRIC, 123_400),))}  # 123.4 m per 100 shirts
    assert by_item(run([demand(SHIRT, 10_000)], boms=boms))[FABRIC].gross_milli == 12_340


def test_a_cycle_is_refused_with_its_path_and_never_loops() -> None:
    boms = {
        "a": MrpBom("a", 1000, (MrpLine("b", 1000),)),
        "b": MrpBom("b", 1000, (MrpLine("c", 1000),)),
        "c": MrpBom("c", 1000, (MrpLine("a", 1000),)),
    }
    with pytest.raises(MrpCycleError) as err:
        run([demand("a", 1_000)], boms=boms)
    assert err.value.path[0] == err.value.path[-1]
    assert set(err.value.path) == {"a", "b", "c"}
    with pytest.raises(MrpCycleError):
        run([demand("a", 1)], boms={"a": MrpBom("a", 1000, (MrpLine("a", 1000),))})


def test_a_diamond_is_not_a_cycle() -> None:
    boms = {
        "top": MrpBom("top", 1000, (MrpLine("l", 1000), MrpLine("r", 1000))),
        "l": MrpBom("l", 1000, (MrpLine("leaf", 1000),)),
        "r": MrpBom("r", 1000, (MrpLine("leaf", 2000),)),
    }
    result = by_item(run([demand("top", 1_000)], boms=boms))
    assert result["leaf"].gross_milli == 3_000 and result["leaf"].level == 2


def test_zero_and_covered_demand_makes_no_shortage() -> None:
    result = run([demand(SHIRT, 3_000)], {SHIRT: Supply(on_hand_milli=3_000)})
    assert result.shortages == []
    assert by_item(result)[SHIRT].action is None


def test_the_result_does_not_depend_on_the_order_demand_arrives_in() -> None:
    demands = [
        demand(SHIRT, 4_000, "SO-A", date(2026, 11, 1)),
        demand(SHIRT, 6_000, "SO-B", date(2026, 12, 1)),
        demand(PANEL, 3_000, "SO-C"),
        demand(FABRIC, 1_500, "SO-D"),
        demand(BUTTON, 20_000, "SO-E", date(2026, 10, 20)),
    ]
    supplies = {PANEL: Supply(on_hand_milli=2_500), FABRIC: Supply(on_hand_milli=9_000)}
    digests = set()
    for seed in range(25):
        shuffled = demands[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311
        digests.add(domain.result_digest(run(shuffled, supplies)))
    assert len(digests) == 1


def test_the_digest_moves_when_the_result_moves() -> None:
    a = domain.result_digest(run([demand(SHIRT, 4_000)]))
    b = domain.result_digest(run([demand(SHIRT, 4_001)]))
    assert a != b and len(a) == 64
