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
#   build → migrate → RESEAL → up → health → RESEAL (again, idempotent)
#
# `reseal_mfa_factors_if_pending` runs from the NEW image (a one-off `docker compose run`, the
# same way `migrate` runs alembic), after the schema is current and before any new container
# serves. It is gated on a SQL count and does nothing — not even start a container — when there is
# nothing to reseal, which is every deploy after the first.
#
# The window, stated plainly: between the reseal finishing and `up` recreating auth, the *old*
# auth container is still serving, and it cannot read a sealed envelope. The old
# `_accept_second_factor` decodes `factor.secret_enc` as UTF-8 *before* it looks at recovery codes,
# and a sealed envelope is never valid UTF-8 — so for those seconds every MFA sign-in, an
# authenticator code AND a recovery code, ends in a 500 for anyone whose factor was just sealed
# (the old code has no MFA lockout, so nothing is counted against them). Accepted: it is seconds
# long, and the alternative order (up, then reseal) is the lockout this exists to prevent.
#
# The same reasoning is why a rollback past this release is not safe: an older ref cannot read the
# sealed factors either, and there is no unseal tool (RUNBOOK, "Roll back a bad deploy").
#
# `reseal_mfa_factors_if_pending` is also called again after `health` (remote.sh `deploy`): a
# plaintext factor a member enrolled through the *old* container inside that window is otherwise
# refused, and answers the "re-seal pending" 503, until the next deploy. It is idempotent.

# The rows still holding a raw base32 secret. `encode(..., 'escape')` renders any bytea — a real
# `pii.seal` envelope leads with a non-printable version byte — so the pattern can only match a
# plaintext row. The RUNBOOK gives operators this same statement to run by hand; a test pins the
# two together.
MFA_PLAINTEXT_COUNT_SQL="select count(*) from auth.mfa_factors where secret_enc is not null and encode(secret_enc,'escape') ~ '^[A-Z2-7]{16,32}\$'"

# _mfa_psql SQL — run one statement in the box's Postgres, printing its result. Deliberately
# leaves stderr alone: when this fails the deploy needs psql's own message, not a silent skip.
_mfa_psql() {
	"${COMPOSE[@]}" exec -T postgres psql -U opsgrids -d opsgrids -Atc "$1"
}

# reseal_mfa_factors_if_pending — the deploy step. Returns non-zero (and so aborts `deploy`
# before `up`) if it cannot tell whether there is anything to reseal, if the reseal fails, or if
# it leaves plaintext behind: serving the new auth with rows it refuses is exactly the lockout.
#
# It skips in exactly one case — the auth table really is absent (a brand-new box before its
# first migrate). "I could not count" is not that case: a postgres that is down or a
# permissions error must not read as "nothing to do".
reseal_mfa_factors_if_pending() {
	local present pending remaining
	present=$(_mfa_psql "select to_regclass('auth.mfa_factors') is not null") || {
		echo "mfa reseal: could not ask postgres whether auth.mfa_factors exists — refusing to continue" >&2
		return 1
	}
	if [ "$(printf %s "$present" | tr -d '[:space:]')" != "t" ]; then
		echo "--- mfa reseal: auth.mfa_factors does not exist yet, skipped"
		return 0
	fi
	pending=$(_mfa_psql "$MFA_PLAINTEXT_COUNT_SQL") || {
		echo "mfa reseal: could not count plaintext MFA factors — refusing to continue" >&2
		return 1
	}
	pending=$(printf %s "$pending" | tr -d '[:space:]')
	case "$pending" in
	'' | *[!0-9]*)
		echo "mfa reseal: unexpected count '$pending' — refusing to continue" >&2
		return 1
		;;
	0)
		echo "--- mfa reseal: no plaintext MFA factors, skipped"
		return 0
		;;
	esac
	log "resealing $pending plaintext MFA factor(s)"
	# --no-deps: postgres is up (preflight proved it) and a changed service definition is not a
	# reason to touch it. The flag goes on this one-off container only; the long-running auth
	# container is never given it.
	"${COMPOSE[@]}" run --rm --no-deps -T -e MFA_ACCEPT_LEGACY_PLAINTEXT=true auth \
		python -m app.mfa_maintenance reseal || {
		echo "mfa reseal failed — refusing to serve auth against factors it cannot read" >&2
		return 1
	}
	remaining=$(_mfa_psql "$MFA_PLAINTEXT_COUNT_SQL") || remaining=""
	remaining=$(printf %s "$remaining" | tr -d '[:space:]')
	if [ "$remaining" != "0" ]; then
		echo "mfa reseal left ${remaining:-an unknown number of} plaintext factor(s) — refusing to continue" >&2
		return 1
	fi
	echo "--- mfa reseal: $pending factor(s) resealed, 0 remain"
}
