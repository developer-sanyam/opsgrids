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

from app.domain import (
    ACTIVATED_KIND,
    CANCELLED_KIND,
    TRIAL_STARTED_KIND,
    RevenueEventRecord,
    apply_org_identity,
    apply_subscription_transition,
    commercial_series,
    month_key,
    month_sequence,
    new_organizations_by_month,
    recompute_snapshot,
)

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
        applied, name, slug, _country, _created_at = apply_org_identity(
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
        applied, name, slug, _country, _created_at = apply_org_identity(
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
        applied, name, slug, _country, _created_at = apply_org_identity(
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
        applied, _name, _slug, _country, _created_at = apply_org_identity(
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
        applied, name, slug, _country, _created_at = apply_org_identity(
            current_name="Northwind Metals",
            current_slug="northwind-metals",
            current_synced_at=_at(0),
            name=None,
            slug=None,
            occurred_at=_at(10),
        )
        assert applied is True
        assert (name, slug) == ("Northwind Metals", "northwind-metals")

    def test_country_and_created_at_fold_the_same_way_as_name(self) -> None:
        applied, _name, _slug, country, created_at = apply_org_identity(
            current_name="Northwind Metals",
            current_slug="northwind-metals",
            current_country=None,
            current_created_at=None,
            current_synced_at=None,
            name="Northwind Metals",
            slug="northwind-metals",
            country="IN",
            created_at=_at(-100),
            occurred_at=_at(0),
        )
        assert applied is True
        assert country == "IN"
        assert created_at == _at(-100)

    def test_an_update_with_no_country_leaves_the_stored_country_alone(self) -> None:
        applied, _name, _slug, country, created_at = apply_org_identity(
            current_name="Northwind Metals",
            current_slug="northwind-metals",
            current_country="IN",
            current_created_at=_at(-100),
            current_synced_at=_at(0),
            name=None,
            slug=None,
            country=None,
            created_at=None,
            occurred_at=_at(10),
        )
        assert applied is True
        assert country == "IN"
        assert created_at == _at(-100)


class TestAGapIsNotStale:
    """A stale source must not overwrite, but may fill what the projection has no value for."""

    def test_a_stale_source_fills_a_missing_country_and_created_at(self) -> None:
        applied, name, slug, country, created_at = apply_org_identity(
            current_name="Northwind Metals",
            current_slug="northwind-metals",
            current_country=None,
            current_created_at=None,
            current_synced_at=_at(10),
            name="Northwind Metals",
            slug="northwind-metals",
            country="IN",
            created_at=_at(-100),
            occurred_at=_at(10),  # a tie with what is stored: stale
        )
        assert applied is False, "name/slug and the sync stamp are not touched"
        assert (name, slug) == ("Northwind Metals", "northwind-metals")
        assert country == "IN"
        assert created_at == _at(-100)

    def test_a_stale_source_never_overwrites_a_value_the_projection_has(self) -> None:
        applied, _name, _slug, country, created_at = apply_org_identity(
            current_name="Northwind Metals",
            current_slug="northwind-metals",
            current_country="IN",
            current_created_at=_at(-100),
            current_synced_at=_at(10),
            name="Old Name",
            slug="old-slug",
            country="US",
            created_at=_at(-5),
            occurred_at=_at(5),
        )
        assert applied is False
        assert country == "IN"
        assert created_at == _at(-100)

    def test_a_stale_source_that_has_nothing_to_offer_fills_nothing(self) -> None:
        _applied, _name, _slug, country, created_at = apply_org_identity(
            current_name="Northwind Metals",
            current_slug="northwind-metals",
            current_country=None,
            current_created_at=None,
            current_synced_at=_at(10),
            name=None,
            slug=None,
            country=None,
            created_at=None,
            occurred_at=_at(5),
        )
        assert country is None and created_at is None


class TestMonthSequence:
    def test_the_last_n_months_end_with_the_given_month_oldest_first(self) -> None:
        keys = month_sequence(end=datetime(2026, 3, 15, tzinfo=UTC), months=4)
        assert keys == ["2025-12", "2026-01", "2026-02", "2026-03"]

    def test_a_single_month_is_just_the_end_months_own_key(self) -> None:
        assert month_sequence(end=datetime(2026, 1, 5, tzinfo=UTC), months=1) == ["2026-01"]

    def test_month_key_pads_single_digit_months(self) -> None:
        assert month_key(datetime(2026, 3, 1, tzinfo=UTC)) == "2026-03"


def _event(
    kind: str,
    *,
    org: str = "org-a",
    product: str = "hrms",
    to_state: str,
    from_state: str | None,
    amount_minor: int = 0,
    occurred_at: str,
    stale: bool = False,
) -> RevenueEventRecord:
    return RevenueEventRecord(
        organization_id=org,
        kind=kind,
        amount_minor=amount_minor,
        occurred_at=occurred_at,
        from_state=from_state,
        to_state=to_state,
        product_key=product,
        stale=stale,
    )


class TestNewOrganizationsByMonth:
    def test_every_month_is_null_with_no_data_at_all(self) -> None:
        result = new_organizations_by_month([], ["2026-01", "2026-02"])
        assert result == {"2026-01": None, "2026-02": None}

    def test_months_before_the_earliest_organization_are_null_not_zero(self) -> None:
        result = new_organizations_by_month(
            ["2026-02-10T00:00:00+00:00"], ["2026-01", "2026-02", "2026-03"]
        )
        assert result == {"2026-01": None, "2026-02": 1, "2026-03": 0}

    def test_counts_more_than_one_organization_in_the_same_month(self) -> None:
        result = new_organizations_by_month(
            ["2026-01-01T00:00:00+00:00", "2026-01-20T00:00:00+00:00"],
            ["2026-01"],
        )
        assert result == {"2026-01": 2}


class TestCommercialSeries:
    """`commercial_series` — MRR replayed from the transition log, and monthly counts of
    trials/conversions/churn from the classification the live fold already recorded."""

    def test_no_events_at_all_is_null_everywhere(self) -> None:
        series = commercial_series([], ["2026-01", "2026-02"])
        assert series["mrr_minor"] == {"2026-01": None, "2026-02": None}
        assert series["trials_started"] == {"2026-01": None, "2026-02": None}
        assert series["trial_conversions"] == {"2026-01": None, "2026-02": None}
        assert series["churned"] == {"2026-01": None, "2026-02": None}

    def test_months_before_the_first_transition_are_null(self) -> None:
        events = [
            _event(
                ACTIVATED_KIND,
                to_state="active",
                from_state=None,
                amount_minor=50000,
                occurred_at="2026-02-10T00:00:00+00:00",
            )
        ]
        series = commercial_series(events, ["2026-01", "2026-02", "2026-03"])
        assert series["mrr_minor"]["2026-01"] is None
        assert series["mrr_minor"]["2026-02"] == 50000
        assert series["mrr_minor"]["2026-03"] == 50000, "MRR carries forward with no new events"

    def test_mrr_rises_on_activation_and_falls_on_cancellation(self) -> None:
        events = [
            _event(
                ACTIVATED_KIND,
                to_state="active",
                from_state=None,
                amount_minor=50000,
                occurred_at="2026-01-05T00:00:00+00:00",
            ),
            _event(
                CANCELLED_KIND,
                to_state="cancelled",
                from_state="active",
                amount_minor=0,
                occurred_at="2026-02-05T00:00:00+00:00",
            ),
        ]
        series = commercial_series(events, ["2026-01", "2026-02", "2026-03"])
        assert series["mrr_minor"] == {"2026-01": 50000, "2026-02": 0, "2026-03": 0}
        assert series["churned"] == {"2026-01": 0, "2026-02": 1, "2026-03": 0}

    def test_a_renewal_at_the_same_price_does_not_inflate_mrr(self) -> None:
        """A renewal re-sets the same product's entry to the same price — replay recomputes the
        snapshot from the current entries, so it never double-counts a subscription across two
        transitions the way summing every row's amount_minor would."""
        events = [
            _event(
                ACTIVATED_KIND,
                to_state="active",
                from_state=None,
                amount_minor=50000,
                occurred_at="2026-01-05T00:00:00+00:00",
            ),
            _event(
                "commerce.subscription.renewed",
                to_state="active",
                from_state="active",
                amount_minor=50000,
                occurred_at="2026-02-05T00:00:00+00:00",
            ),
        ]
        series = commercial_series(events, ["2026-01", "2026-02"])
        assert series["mrr_minor"] == {"2026-01": 50000, "2026-02": 50000}

    def test_mrr_sums_across_organizations(self) -> None:
        events = [
            _event(
                ACTIVATED_KIND,
                org="org-a",
                to_state="active",
                from_state=None,
                amount_minor=50000,
                occurred_at="2026-01-05T00:00:00+00:00",
            ),
            _event(
                ACTIVATED_KIND,
                org="org-b",
                to_state="active",
                from_state=None,
                amount_minor=30000,
                occurred_at="2026-01-10T00:00:00+00:00",
            ),
        ]
        series = commercial_series(events, ["2026-01"])
        assert series["mrr_minor"]["2026-01"] == 80000

    def test_trials_started_and_conversions_are_counted_by_month(self) -> None:
        events = [
            _event(
                TRIAL_STARTED_KIND,
                to_state="trial",
                from_state=None,
                occurred_at="2026-01-05T00:00:00+00:00",
            ),
            _event(
                ACTIVATED_KIND,
                to_state="active",
                from_state="trial",
                amount_minor=50000,
                occurred_at="2026-02-05T00:00:00+00:00",
            ),
        ]
        series = commercial_series(events, ["2026-01", "2026-02"])
        assert series["trials_started"] == {"2026-01": 1, "2026-02": 0}
        assert series["trial_conversions"] == {"2026-01": 0, "2026-02": 1}

    def test_a_trial_extension_is_not_a_trial_start(self) -> None:
        """ADR-0031: `trial_extended` is a `trial -> trial` move of its own kind. It replays
        through the fold (MRR stays zero) and counts as neither a start nor a conversion."""
        events = [
            _event(
                TRIAL_STARTED_KIND,
                to_state="trial",
                from_state=None,
                occurred_at="2026-01-05T00:00:00+00:00",
            ),
            _event(
                "commerce.subscription.trial_extended",
                to_state="trial",
                from_state="trial",
                occurred_at="2026-01-19T00:00:00+00:00",
            ),
            _event(
                ACTIVATED_KIND,
                to_state="active",
                from_state="trial",
                amount_minor=50000,
                occurred_at="2026-02-05T00:00:00+00:00",
            ),
        ]
        series = commercial_series(events, ["2026-01", "2026-02"])
        assert series["trials_started"] == {"2026-01": 1, "2026-02": 0}
        assert series["trial_conversions"] == {"2026-01": 0, "2026-02": 1}
        assert series["mrr_minor"] == {"2026-01": 0, "2026-02": 50000}

    def test_a_stale_transition_is_excluded_from_every_count(self) -> None:
        """A DLQ replay recorded with `stale=True` must not be double-counted as a second
        trial-conversion or churn, even though it still moves through the replay."""
        events = [
            _event(
                ACTIVATED_KIND,
                to_state="active",
                from_state="trial",
                amount_minor=50000,
                occurred_at="2026-01-05T00:00:00+00:00",
            ),
            _event(
                ACTIVATED_KIND,
                to_state="active",
                from_state="trial",
                amount_minor=50000,
                occurred_at="2026-01-01T00:00:00+00:00",
                stale=True,
            ),
        ]
        series = commercial_series(events, ["2026-01"])
        assert series["trial_conversions"]["2026-01"] == 1
