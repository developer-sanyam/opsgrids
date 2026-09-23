#!/usr/bin/env bash
# Zero-activation profile auto-off (ADR-0013's memory-budget erratum, 2026-09-23: "the only
# lever here that costs nothing to ship — no code change to a product, no new service").
#
# A compose *profile* is the operational switch that starts a product's containers; the
# catalogue's `status` is the commercial one ("this deployment sells it"); `preflight.sh`
# already checks those two agree. Neither ever asked the third question this file answers:
# has any organization actually activated the product? A profile can be `available` and
# enabled and still cost this box a full container group for zero customers, indefinitely,
# because nothing ever turned it back off.
#
# Sourced by preflight.sh and remote.sh. `profile_for` and `activated_profiles` are pure string
# functions — no `set -e`, no side effects, no network — tested directly by
# tests/infra/test_active_profiles.py with no database. The query that actually reaches
# Postgres (`select distinct product_key from product.activations`) is two lines in each
# caller, kept out of this file on purpose, the same split `memory-budget.sh` uses for
# `memory_budget_verdict`.

# profile_for <product-key> — the compose profile that runs a catalogue product key, or empty
# for a platform product (notification) or a key this box does not catalogue as a profile.
# The same mapping preflight.sh's catalogue/profile agreement check has always used.
profile_for() {
	case "$1" in
	operations) echo inventory ;;
	storefront) echo storefront ;;
	hrms | crm | pulseboard | projects | support | marketing | finance | itsm) echo "$1" ;;
	*) echo "" ;;
	esac
}

# activated_profiles <product-key>... — the deduplicated list of compose profiles, one per
# line, that have at least one activation among the product keys given. Order follows first
# appearance; a key with no compose profile is silently skipped, the same rule the catalogue
# agreement check already applies. Takes its input as arguments (not stdin) so a caller can
# word-split a psql column straight into it with no subshell.
activated_profiles() {
	local key profile seen=" "
	for key in "$@"; do
		profile=$(profile_for "$key")
		[ -n "$profile" ] || continue
		case "$seen" in
		*" $profile "*) continue ;;
		esac
		seen="$seen$profile "
		echo "$profile"
	done
}
