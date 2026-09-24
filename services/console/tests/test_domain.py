"""The pure fold — ordering guard, tombstone, and snapshot arithmetic. No I/O, no database.

`apply_subscription_transition` used to be last-write-wins over unordered delivery: a suspension
delivered after the reactivation that superseded it left the org locked, and cancellation *popped*
the product's entry, so a stale pre-cancellation event could resurrect it with nothing left to
compare against. The fold now returns ``(applied, from_state)``, drops anything whose
`occurred_at` does not advance the entry's, and keeps a cancelled entry as a tombstone that
contributes nothing to the snapshot but keeps guarding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain import apply_org_identity, apply_subscription_transition, recompute_snapshot

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _at(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _apply(
    states: dict[str, Any],
    *,
    to_state: str,
    occurred_at: str | None,
    product_key: str = "hrms",
    amount_minor: int = 50000,
    currency: str | None = "INR",
    trial_ends_at: str | None = None,
) -> tuple[bool, str | None]:
    return apply_subscription_transition(
        states,
        product_key=product_key,
        to_state=to_state,
        plan_version_id="pv-1",
        amount_minor=amount_minor,
        currency=currency,
        trial_ends_at=trial_ends_at,
        occurred_at=occurred_at,
    )


class TestOrderingGuard:
    def test_in_order_transitions_apply_and_classify(self) -> None:
        """The baseline: each newer transition applies, and `from_state` still classifies —
        `None` for a brand-new subscription, `active` for the churn read."""
        states: dict[str, Any] = {}

        assert _apply(states, to_state="active", occurred_at=_at(0)) == (True, None)
        assert states["hrms"]["state"] == "active"

        applied, from_state = _apply(states, to_state="cancelled", occurred_at=_at(10))
        assert applied is True
        assert from_state == "active", "active → cancelled must still read as churn"

    def test_a_stale_transition_is_dropped_untouched(self) -> None:
        """An event whose `occurred_at` does not advance the entry's returns (False, None) and
        leaves `states` byte-for-byte alone — including the tie, which must not re-apply."""
        states: dict[str, Any] = {}
        _apply(states, to_state="active", occurred_at=_at(10))
        before = {k: dict(v) for k, v in states.items()}

        assert _apply(states, to_state="suspended", occurred_at=_at(5)) == (False, None)
        assert states == before, "a stale transition mutated the states it was dropped from"

        assert _apply(states, to_state="suspended", occurred_at=_at(10)) == (False, None)
        assert states == before

    def test_a_reversed_suspend_reactivate_does_not_lock(self) -> None:
        """The regression that motivated the guard: suspension and reactivation delivered in the
        wrong order left the org locked with zero MRR until the next transition."""
        states: dict[str, Any] = {}
        _apply(states, to_state="active", occurred_at=_at(0))
        _apply(states, to_state="active", occurred_at=_at(20))  # the reactivation, arriving early
        applied, _ = _apply(states, to_state="suspended", occurred_at=_at(10))  # the stale suspend

        assert applied is False
        snapshot = recompute_snapshot(states)
        assert snapshot["locked"] is False, "a stale suspension locked a reactivated org"
        assert snapshot["mrr_minor"] == 50000

    def test_legacy_entries_without_occurred_at_still_fold(self) -> None:
        """Rows written before the guard carry no `occurred_at`; nothing to compare, so any
        transition folds — and an incoming event without a timestamp folds too."""
        states: dict[str, Any] = {
            "hrms": {"state": "active", "amount_minor": 50000, "currency": "INR"}
        }
        applied, from_state = _apply(states, to_state="cancelled", occurred_at=_at(0))
        assert (applied, from_state) == (True, "active")

        states = {"crm": {"state": "active", "occurred_at": _at(0), "amount_minor": 1}}
        applied, from_state = _apply(
            states, to_state="suspended", occurred_at=None, product_key="crm"
        )
        assert (applied, from_state) == (True, "active")


class TestCancellationTombstone:
    def test_a_stale_activation_cannot_resurrect_a_cancelled_product(self) -> None:
        """Why cancellation tombstones instead of popping: with the entry gone, a stale
        pre-cancellation `activated` replay had nothing to compare against and re-entitled the
        product. The tombstone keeps the `occurred_at` the guard needs."""
        states: dict[str, Any] = {}
        _apply(states, to_state="active", occurred_at=_at(0))
        _apply(states, to_state="cancelled", occurred_at=_at(20))
        assert states["hrms"] == {"state": "cancelled", "occurred_at": _at(20)}

        applied, from_state = _apply(states, to_state="active", occurred_at=_at(10))
        assert (applied, from_state) == (False, None)
        assert states["hrms"]["state"] == "cancelled", (
            "a stale activation resurrected a cancelled product"
        )
        assert "hrms" not in recompute_snapshot(states)["active_products"]

    def test_a_cancelled_tombstone_contributes_nothing_to_the_snapshot(self) -> None:
        """The tombstone is bookkeeping, not state: no entitlement, no MRR, no lock — the
        snapshot reads exactly as it did when cancellation popped the key."""
        states: dict[str, Any] = {
            "hrms": {"state": "cancelled", "occurred_at": _at(0)},
            "crm": {
                "state": "active",
                "plan_version_id": "pv-1",
                "amount_minor": 30000,
                "currency": "INR",
                "trial_ends_at": None,
                "occurred_at": _at(5),
            },
        }
        snapshot = recompute_snapshot(states)
        assert snapshot["active_products"] == ["crm"]
        assert snapshot["mrr_minor"] == 30000, "a cancelled tombstone added to MRR"
        assert snapshot["locked"] is False, "a cancelled tombstone locked the org"
        assert snapshot["currency"] == "INR"


class TestOrgIdentity:
    """`apply_org_identity` — the same ordering guard as a subscription transition, over the
    thinner signal of a `core.org.*` event or a backfill snapshot."""

    def test_the_first_sighting_always_applies(self) -> None:
        applied, name, slug = apply_org_identity(
            current_name=None,
            current_slug=None,
            current_synced_at=None,
            name="Northwind Metals",
            slug="northwind-metals",
            occurred_at=_at(0),
        )
        assert applied is True
        assert (name, slug) == ("Northwind Metals", "northwind-metals")

    def test_a_later_rename_overwrites_the_name(self) -> None:
        applied, name, slug = apply_org_identity(
            current_name="Old Name",
            current_slug="old-slug",
            current_synced_at=_at(0),
            name="New Name",
            slug=None,
            occurred_at=_at(10),
        )
        assert applied is True
        assert name == "New Name"
        assert slug == "old-slug", "an update with no slug field must not blank the stored one"

    def test_a_stale_source_is_dropped_untouched(self) -> None:
        """An event replayed after a later rename, or a backfill run against data already
        superseded by a real event, must not resurrect the old name."""
        applied, name, slug = apply_org_identity(
            current_name="New Name",
            current_slug="new-slug",
            current_synced_at=_at(10),
            name="Old Name",
            slug="old-slug",
            occurred_at=_at(5),
        )
        assert applied is False
        assert (name, slug) == ("New Name", "new-slug")

    def test_a_tie_does_not_reapply(self) -> None:
        """Re-running the backfill against unchanged source data — the same `updated_at` it wrote
        last time — must be a no-op, not a redundant write."""
        applied, _name, _slug = apply_org_identity(
            current_name="Northwind Metals",
            current_slug="northwind-metals",
            current_synced_at=_at(10),
            name="Northwind Metals",
            slug="northwind-metals",
            occurred_at=_at(10),
        )
        assert applied is False

    def test_an_update_with_no_name_leaves_the_stored_name_alone(self) -> None:
        """`core.org.updated`'s payload carries `name: null` when `name` was not in `changed` —
        this must not blank a name that was never touched."""
        applied, name, slug = apply_org_identity(
            current_name="Northwind Metals",
            current_slug="northwind-metals",
            current_synced_at=_at(0),
            name=None,
            slug=None,
            occurred_at=_at(10),
        )
        assert applied is True
        assert (name, slug) == ("Northwind Metals", "northwind-metals")
