#!/usr/bin/env bash
# The catalogue product key <-> compose profile mapping, and the advisory check built on it
# (memory-headroom slice, 2026-09-23 fix round; H2 review finding).
#
# **This file used to also compute COMPOSE_PROFILES automatically from activations. It no
# longer does.** `docker compose up` does not stop a container whose profile was just removed
# from COMPOSE_PROFILES — it only starts what a profile *adds*, never tears down what a changed
# profile list *removes* (an "orphan" in compose's own vocabulary is a container whose *service*
# no longer exists in the file at all, not one merely filtered out by profile). A script quietly
# changing COMPOSE_PROFILES between deploys therefore does not change what is actually running —
# it only changes what `preflight.sh` and `remote.sh` *believe* is running, which is worse than
# doing nothing: a silent, wrong belief about the box's own state. `EDGE_PROFILES`/
# `COMPOSE_PROFILES` are explicit and stay explicit; a human decides what runs and lists it.
#
# What this file's `activated_profiles` now backs is a pure **advisory** in `preflight.sh`:
# a product with real activations whose profile is not enabled is refused (an actual customer
# would lose service); a profile enabled with zero activations, for a product the catalogue does
# not even sell, is reported as information. Neither ever changes COMPOSE_PROFILES itself.
#
# Sourced by preflight.sh. `profile_for`, `key_for_profile` and `activated_profiles` are pure
# string functions — no `set -e`, no side effects, no network — tested directly by
# tests/infra/test_active_profiles.py with no database. The query that actually reaches
# Postgres (`select distinct product_key from product.activations`) is a couple of lines in the
# caller, kept out of this file on purpose, the same split `memory-budget.sh` uses for
# `memory_budget_verdict`.

# profile_for <product-key> — the compose profile that runs a catalogue product key, or empty
# for a platform product (notification) or a key this box does not catalogue as a profile.
profile_for() {
	case "$1" in
	operations) echo inventory ;;
	storefront) echo storefront ;;
	hrms | crm | pulseboard | projects | support | marketing | finance | itsm) echo "$1" ;;
	*) echo "" ;;
	esac
}

# key_for_profile <compose-profile> — the inverse of profile_for: the catalogue product key a
# compose profile serves, or empty for a profile this file does not know. Every profile in
# memory-budget.json's own `profiles` list round-trips through profile_for/key_for_profile
# (tests/infra/test_active_profiles.py pins this), so the two mappings can never quietly drift
# apart the way two independently hand-maintained `case` statements eventually would have
# (preflight.sh used to keep its own copy of this exact reverse mapping inline — L10, the same
# fix round).
key_for_profile() {
	case "$1" in
	inventory) echo operations ;;
	storefront) echo storefront ;;
	hrms | crm | pulseboard | projects | support | marketing | finance | itsm) echo "$1" ;;
	*) echo "" ;;
	esac
}

# resolve_declared_profiles <edge_profiles> <compose_profiles> — the one profile list a box
# actually declares, given both raw values as read from `.env`/the shell environment.
# EDGE_PROFILES wins whenever it is non-empty; COMPOSE_PROFILES otherwise, including when both
# are empty (prints empty, meaning core only — never an error). Pure and side-effect free, so
# preflight.sh and remote.sh share this one implementation instead of two hand-written `if`s that
# could quietly drift apart (the same reasoning `key_for_profile` documents for `profile_for`).
resolve_declared_profiles() {
	local edge_profiles="${1:-}" compose_profiles="${2:-}"
	if [ -n "$edge_profiles" ]; then
		echo "$edge_profiles"
	else
		echo "$compose_profiles"
	fi
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
