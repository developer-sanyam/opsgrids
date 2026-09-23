#!/usr/bin/env bash
# Sourced by remote.sh, and by tests/infra/test_mfa_reseal.py directly — no side effects of its
# own (no `cd`, no `set -e`), the same shape as lib/role-reconcile.sh and lib/health-check.sh.
# Expects the caller to have defined COMPOSE (an array: `docker compose -f ... --env-file ...`)
# and `log`.
#
# The problem this closes (R1 of the final security review of the operator-MFA slice): a box
# deployed from `main` stores every TOTP secret — tenant members' and staff alike — as raw
# plaintext in `auth.mfa_factors.secret_enc`. The new auth code refuses a plaintext row unless
# `MFA_ACCEPT_LEGACY_PLAINTEXT` is set, because nothing authenticates one. Left as a manual
# post-deploy step, that is a lockout: every member with MFA is unreadable from the moment the
# new containers serve until somebody remembers the runbook. So the deploy does it, and does it
# in the one place it can be made safe:
#
#   build → migrate → RESEAL → up → health
#
# `reseal_mfa_factors_if_pending` runs from the NEW image (a one-off `docker compose run`, the
# same way `migrate` runs alembic), after the schema is current and before any new container
# serves. It is gated on a SQL count and does nothing — not even start a container — when there is
# nothing to reseal, which is every deploy after the first.
#
# The window, stated plainly: between the reseal finishing and `up` recreating auth, the *old*
# auth container is still serving, and it cannot read a sealed envelope. For those seconds a member
# who signs in with an authenticator code is refused (the old code has no MFA lockout, so nothing
# is counted against them); a recovery code still works, since those are hashes and are not
# touched. Accepted: the alternative order (up, then reseal) is the lockout this exists to
# prevent.

# The rows still holding a raw base32 secret. `encode(..., 'escape')` renders any bytea — a real
# `pii.seal` envelope leads with a non-printable version byte — so the pattern can only match a
# plaintext row. The RUNBOOK gives operators this same statement to run by hand; a test pins the
# two together.
MFA_PLAINTEXT_COUNT_SQL="select count(*) from auth.mfa_factors where secret_enc is not null and encode(secret_enc,'escape') ~ '^[A-Z2-7]{16,32}\$'"

# mfa_plaintext_count — prints the number, or nothing if it cannot be counted (the auth schema
# does not exist yet, postgres is not answering). Never fails the caller: "cannot count" is
# handled by the caller as "skip", not as an error, because on a brand-new box there is no
# auth schema until migrate has run and no factor to reseal either.
mfa_plaintext_count() {
	"${COMPOSE[@]}" exec -T postgres psql -U opsgrids -d opsgrids -Atc "$MFA_PLAINTEXT_COUNT_SQL" 2>/dev/null | tr -d '[:space:]' || true
}

# reseal_mfa_factors_if_pending — the deploy step. Returns non-zero (and so aborts `deploy`
# before `up`) if the reseal fails or leaves plaintext behind: serving the new auth with rows
# it refuses is exactly the lockout.
reseal_mfa_factors_if_pending() {
	local pending remaining
	pending=$(mfa_plaintext_count)
	case "$pending" in
	'' | *[!0-9]*)
		echo "--- mfa reseal: cannot count legacy factors (no auth schema yet?), skipped"
		return 0
		;;
	0)
		echo "--- mfa reseal: no plaintext MFA factors, skipped"
		return 0
		;;
	esac
	log "resealing $pending plaintext MFA factor(s) (before the new version serves)"
	# --no-deps: postgres is up (preflight proved it) and a changed service definition is not a
	# reason to touch it. The flag goes on this one-off container only; the long-running auth
	# container is never given it.
	"${COMPOSE[@]}" run --rm --no-deps -T -e MFA_ACCEPT_LEGACY_PLAINTEXT=true auth \
		python -m app.mfa_maintenance reseal || {
		echo "mfa reseal failed — refusing to serve auth against factors it cannot read" >&2
		return 1
	}
	remaining=$(mfa_plaintext_count)
	if [ "$remaining" != "0" ]; then
		echo "mfa reseal left ${remaining:-an unknown number of} plaintext factor(s) — refusing to continue" >&2
		return 1
	fi
	echo "--- mfa reseal: $pending factor(s) resealed, 0 remain"
}
