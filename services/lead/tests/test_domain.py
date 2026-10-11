"""Pure rules: the lifecycle transitions and the round-robin rotation. No I/O, no session —
these run in plain Python and never need a database.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from app import domain


class TestTransitions:
    def test_new_moves_to_working_or_disqualified(self) -> None:
        assert domain.can_transition("new", "working")
        assert domain.can_transition("new", "disqualified")
        assert not domain.can_transition("new", "qualified")

    def test_nothing_moves_directly_to_converted(self) -> None:
        for status in domain.STATUS_ORDER:
            assert not domain.can_transition(status, "converted")

    def test_converted_is_terminal(self) -> None:
        assert not domain.can_transition("converted", "new")
        assert not domain.can_transition("converted", "working")

    def test_disqualified_can_be_reopened(self) -> None:
        assert domain.can_transition("disqualified", "new")
        assert domain.can_transition("disqualified", "working")

    def test_a_status_does_not_transition_to_itself(self) -> None:
        assert not domain.can_transition("working", "working")

    def test_unknown_statuses_are_refused(self) -> None:
        assert not domain.can_transition("new", "bogus")
        assert not domain.can_transition("bogus", "new")


class TestCanConvert:
    def test_only_qualified_may_convert(self) -> None:
        assert domain.can_convert("qualified")
        for status in ("new", "working", "disqualified", "converted"):
            assert not domain.can_convert(status)


class TestConversionIds:
    def test_the_same_lead_id_always_derives_the_same_three_ids(self) -> None:
        lead_id = uuid.uuid4()
        first = domain.conversion_ids(lead_id)
        second = domain.conversion_ids(lead_id)
        assert first == second

    def test_different_leads_derive_different_ids(self) -> None:
        company_a, contact_a, deal_a = domain.conversion_ids(uuid.uuid4())
        company_b, contact_b, deal_b = domain.conversion_ids(uuid.uuid4())
        assert {company_a, contact_a, deal_a}.isdisjoint({company_b, contact_b, deal_b})

    def test_the_three_ids_for_one_lead_are_distinct(self) -> None:
        ids = domain.conversion_ids(uuid.uuid4())
        assert len(set(ids)) == 3

    def test_generation_zero_derives_exactly_the_ids_the_function_always_has(self) -> None:
        """Nothing already handed out may change: a lead converted before generations existed
        must still derive the same three ids from its own id."""
        lead_id = uuid.uuid4()
        ns = domain._CONVERSION_NAMESPACE
        assert domain.conversion_ids(lead_id) == domain.conversion_ids(lead_id, 0)
        assert domain.conversion_ids(lead_id, 0) == (
            uuid.uuid5(ns, f"{lead_id}:company"),
            uuid.uuid5(ns, f"{lead_id}:contact"),
            uuid.uuid5(ns, f"{lead_id}:deal"),
        )

    def test_each_generation_is_a_fresh_reproducible_set(self) -> None:
        lead_id = uuid.uuid4()
        first = domain.conversion_ids(lead_id, 1)
        assert first == domain.conversion_ids(lead_id, 1)
        assert set(first).isdisjoint(domain.conversion_ids(lead_id, 0))
        assert set(first).isdisjoint(domain.conversion_ids(lead_id, 2))


class TestDealName:
    def test_a_short_name_is_returned_with_its_lead_id_suffix_untouched(self) -> None:
        lead_id = uuid.uuid4()
        name = domain.deal_name("Stellar Robotics", "Priya Sharma", lead_id)
        assert name == f"Stellar Robotics — Priya Sharma · {str(lead_id)[:8]}"
        assert len(name) <= domain.DEAL_NAME_MAX_LENGTH

    def test_a_long_company_and_person_name_is_truncated_to_fit(self) -> None:
        lead_id = uuid.uuid4()
        company = "A" * 150
        full_name = "B" * 150
        name = domain.deal_name(company, full_name, lead_id)
        assert len(name) <= domain.DEAL_NAME_MAX_LENGTH
        assert name.endswith(f" · {str(lead_id)[:8]}")
        assert "…" in name

    def test_two_leads_naming_the_same_company_and_person_get_distinct_deal_names(self) -> None:
        first = domain.deal_name("Stellar Robotics", "Priya Sharma", uuid.uuid4())
        second = domain.deal_name("Stellar Robotics", "Priya Sharma", uuid.uuid4())
        assert first != second

    def test_generation_zero_names_are_exactly_what_they_always_were(self) -> None:
        lead_id = uuid.uuid4()
        legacy = f"Stellar Robotics — Priya Sharma · {str(lead_id)[:8]}"
        assert domain.deal_name("Stellar Robotics", "Priya Sharma", lead_id) == legacy
        assert domain.deal_name("Stellar Robotics", "Priya Sharma", lead_id, 0) == legacy

    def test_each_later_generation_gets_its_own_name(self) -> None:
        """Deal-service refuses a second live deal with the same name, so a bumped generation
        that reused the old name would fail on the deal instead of on the ids."""
        lead_id = uuid.uuid4()
        names = [domain.deal_name("Stellar Robotics", "Priya Sharma", lead_id, g) for g in range(4)]
        assert len(set(names)) == 4
        assert names[1].endswith(f" · {str(lead_id)[:8]}/1")
        assert names[3].endswith("/3")

    def test_a_long_name_stays_bounded_with_a_generation_suffix(self) -> None:
        lead_id = uuid.uuid4()
        for generation in (0, 1, 12, 345):
            name = domain.deal_name("C" * 300, "P" * 300, lead_id, generation)
            assert len(name) <= domain.DEAL_NAME_MAX_LENGTH
            assert name.endswith(f"/{generation}" if generation else str(lead_id)[:8])

    def test_the_result_never_exceeds_the_column_width_even_at_the_boundary(self) -> None:
        lead_id = uuid.uuid4()
        # Exactly at, and one past, the budget before truncation kicks in.
        for length in (185, 186, 187, 200, 500):
            name = domain.deal_name("C" * length, "P" * length, lead_id)
            assert len(name) <= domain.DEAL_NAME_MAX_LENGTH


class TestRoundRobin:
    def test_an_empty_rotation_assigns_nobody(self) -> None:
        owner, cursor = domain.next_owner([], 0)
        assert owner is None
        assert cursor == 0

    def test_it_rotates_through_the_list_in_order(self) -> None:
        owners = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        cursor = 0
        picked = []
        for _ in range(6):
            owner, cursor = domain.next_owner(owners, cursor)
            picked.append(owner)
        assert picked == owners + owners

    def test_a_cursor_past_the_list_wraps_rather_than_raising(self) -> None:
        """A cursor left over from a longer rotation (an owner was removed) still lands on a
        valid slot, and the stored cursor is re-based to the small range rather than kept
        growing unbounded."""
        owners = [uuid.uuid4(), uuid.uuid4()]
        owner, cursor = domain.next_owner(owners, 101)
        assert owner == owners[1]  # 101 % 2 == 1
        assert cursor == 2


class TestConversionIdVectors:
    """Lead's `conversion_ids` is the original; deal and customer hold copies that the same
    committed vectors pin (`libs/contracts/vectors/conversion_ids.v1.json`)."""

    VECTORS = (
        Path(__file__).resolve().parents[3]
        / "libs"
        / "contracts"
        / "vectors"
        / "conversion_ids.v1.json"
    )

    def test_conversion_ids_match_the_committed_vectors(self) -> None:
        data = json.loads(self.VECTORS.read_text())
        assert len(data["vectors"]) == 24
        for v in data["vectors"]:
            got = domain.conversion_ids(uuid.UUID(v["lead_id"]), v["generation"])
            assert tuple(str(i) for i in got) == (v["company_id"], v["contact_id"], v["deal_id"])

    def test_generation_zero_has_no_suffix(self) -> None:
        lead_id = uuid.uuid4()
        company, _, _ = domain.conversion_ids(lead_id, 0)
        assert company == uuid.uuid5(domain._CONVERSION_NAMESPACE, f"{lead_id}:company")
        assert domain.conversion_ids(lead_id, 1)[0] != company

    def test_the_namespace_constant_equals_the_vector_files_namespace(self) -> None:
        data = json.loads(self.VECTORS.read_text())
        assert str(domain._CONVERSION_NAMESPACE) == data["namespace"]
