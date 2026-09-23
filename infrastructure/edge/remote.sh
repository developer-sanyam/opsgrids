#!/usr/bin/env bash
# The half of a deploy that runs ON THE EDGE VM. Never run this on a laptop.
#
# It is a script rather than a line in the Makefile because it ships with the commit being
# deployed: `make deploy` checks out a ref and then runs *that ref's* copy of this file, so
# the deploy procedure and the thing being deployed can never be a version apart.
#
# Order is the whole point:
#
#   preflight  → nothing has changed yet, and this is where a wrong box or a missing secret
#                stops the deploy.
#   build      → images are built with the running containers untouched. A build that fails
#                here leaves the site exactly as it was. This is the only protection slice 1
#                has against "a failed build leaves a merchant's shop down", and it is real
#                but partial: see `up`.
#   validate   → the Caddyfile is loaded by the pinned Caddy binary before anything restarts.
#                A Caddyfile that fails to load is a total outage and there is no staging.
#   migrate    → migrations run BEFORE the new containers serve, and must be backward
#                compatible with the version currently running, because for the length of the
#                deploy both are true at once.
#   up         → containers are recreated. THIS IS NOT ZERO DOWNTIME. Each container that
#                changed is stopped and started; buyers see errors for a few seconds, and if
#                Caddy itself changed, for as long as it takes to start. Slice 1 does not have
#                a second box and does not pretend otherwise.
#
# Usage: remote.sh <command> [args]
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# shellcheck source=lib/rebuild-decision.sh
source lib/rebuild-decision.sh
# shellcheck source=lib/health-check.sh
source lib/health-check.sh

COMPOSE=(docker compose -f compose.prod.yml --env-file .env)

# What the gateway's container is keyed on (see ROUTES_SHA in compose.prod.yml): the content of
# the route table at this ref. Exported so every compose call below renders the same value.
ROUTES_SHA=$(sha256sum routes.prod.json | cut -c1-16)
export ROUTES_SHA
STATE_DIR="${EDGE_STATE_DIR:-/srv/opsgrids/state}"
# Every service that owns a schema. Migrated in this order, but only the ones compose will
# actually run — a product behind a disabled profile (ADR-0013 §5) is skipped, and picked up
# on the deploy that enables it.
SERVICES_WITH_SCHEMA=(auth account product catalog subscription billing metering payment notification storefront document operations approval employee attendance customer deal project timesheet standup projects support marketing finance itsm)

# The services compose will run for this box: honours COMPOSE_PROFILES from .env.
configured_services() {
	"${COMPOSE[@]}" config --services
}

log() { printf '\n=== %s\n' "$*"; }

# ---------------------------------------------------------------- the wrong-box interlock
assert_host() {
	local claimed="${1:-}"
	if [ -z "$claimed" ]; then
		echo "assert-host: no host named. Deploys must name their target." >&2
		exit 2
	fi
	if [ ! -f .env ]; then
		echo "assert-host: no infrastructure/edge/.env on $(hostname)." >&2
		echo "  This box has never been configured as an OpsGrids edge. If you meant to" >&2
		echo "  bootstrap it, follow RUNBOOK.md 'standing up a new box' by hand — a deploy" >&2
		echo "  will not do it for you." >&2
		exit 2
	fi
	local actual
	actual=$(sed -n 's/^EDGE_HOST=//p' .env | head -1 | tr -d '"'"'"' \r')
	if [ "$actual" != "$claimed" ]; then
		echo "assert-host: REFUSING." >&2
		echo "  You asked to deploy to: $claimed" >&2
		echo "  This box says it is:    ${actual:-<unset>}   ($(hostname))" >&2
		echo "  Nothing was changed." >&2
		exit 2
	fi
	echo "assert-host: ok — $(hostname) is $actual"
}

# The ref this box last served healthily, if it is one this clone still knows. Everything that
# asks "what changed?" asks it against this, not against the previous commit: what matters is
# what the box does not have yet, and a deploy that failed half way leaves that further back
# than HEAD~1.
good_ref() {
	local prev
	prev=$(cat "$STATE_DIR/last-good-ref" 2>/dev/null || true)
	[ -n "$prev" ] && git -C ../.. rev-parse -q --verify "$prev^{commit}" >/dev/null 2>&1 && echo "$prev"
	return 0
}

build() {
	log "building images (running containers are untouched until 'up')"
	# FORCE_PULL=1 re-resolves the base image tags. Not the default: those tags move about
	# weekly, a moved tag invalidates the shared venv layer under all ~25 images, and a full
	# rebuild on two cores is twenty-five minutes at a moment nobody chose.
	local pull=() prev svc to_build=()
	[ "${FORCE_PULL:-0}" = "1" ] && pull=(--pull)
	prev=$(good_ref)
	# Anything every image is made from changed, or there is no baseline: build them all.
	# The pathspec and the reasoning live beside `needs_full_rebuild` in
	# lib/rebuild-decision.sh, which tests/infra/test_remote_rebuild_decision.py pins down —
	# a wrong rule here means production silently runs stale images.
	if [ "${FORCE_PULL:-0}" = "1" ] || needs_full_rebuild ../.. "$prev" HEAD; then
		"${COMPOSE[@]}" build ${pull[@]+"${pull[@]}"}
		return
	fi
	# Otherwise only the services whose own directory changed, or whose image is not here yet
	# (a profile that was just switched on).
	for svc in $(configured_services); do
		[ -f "../../services/$svc/Dockerfile" ] || continue
		if ! git -C ../.. diff --quiet "$prev" HEAD -- "services/$svc" ||
			! docker image inspect "opsgrids-edge/$svc" >/dev/null 2>&1; then
			to_build+=("$svc")
		fi
	done
	if [ ${#to_build[@]} -eq 0 ]; then
		echo "no service changed since $prev: nothing to build"
		return
	fi
	echo "changed since $prev: ${to_build[*]}"
	COMPOSE_PARALLEL_LIMIT=2 "${COMPOSE[@]}" build "${to_build[@]}"
}

validate() {
	log "validating the Caddyfile against the pinned Caddy image"
	# --no-deps so this cannot start anything, -T because there is no terminal over ssh.
	"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint caddy caddy \
		validate --config /etc/caddy/Caddyfile --adapter caddyfile
}

migrate() {
	log "migrations (before the new version serves)"
	local running prev migrated
	running=" $(configured_services | tr '\n' ' ') "
	prev=$(good_ref)
	# Schemas alembic has already stamped. A service whose profile was just switched on has
	# unchanged migrations and no tables, so "unchanged since the good ref" alone is not enough.
	migrated=" $("${COMPOSE[@]}" exec -T postgres psql -U opsgrids -d opsgrids -Atc \
		"select table_schema from information_schema.tables where table_name = 'alembic_version'" 2>/dev/null | tr '\n' ' ') "
	for svc in "${SERVICES_WITH_SCHEMA[@]}"; do
		case "$running" in
		*" $svc "*) ;;
		*)
			echo "--- $svc: not in this box's profiles, skipped"
			continue
			;;
		esac
		# Each run below is a fresh interpreter importing the service's models and alembic —
		# ~180 MB on top of a full box, two dozen times in a row. That was the swap storm in
		# every deploy, and on a typical deploy none of them had anything to apply.
		if [ -n "$prev" ] && [ "${FORCE_MIGRATE:-0}" != "1" ]; then
			case "$migrated" in
			*" $svc "*)
				if git -C ../.. diff --quiet "$prev" HEAD -- "services/$svc/migrations" "services/$svc/alembic.ini"; then
					echo "--- $svc: migrations unchanged since $prev, skipped"
					continue
				fi
				;;
			esac
		fi
		echo "--- $svc"
		# Each image carries its own alembic.ini and migrations/, and env.py connects as the
		# admin role. No foreign key crosses a schema, so the order between services does not
		# matter; they are run one at a time because this box has four vCPU and is serving.
		# --no-deps: preflight has proved Postgres is up, and compose must not take a changed
		# service definition as a reason to touch postgres/redis/rabbitmq mid-migration.
		"${COMPOSE[@]}" run --rm --no-deps -T "$svc" alembic upgrade head
	done
}

up() {
	log "recreating containers (brief downtime for anything that changed)"
	# Four at a time. Unbounded, a deploy that touches the shared environment starts every
	# container at once, and ~59 Python interpreters importing SQLAlchemy on two cores is a
	# load average of 7 and health checks timing out for no reason but the stampede.
	COMPOSE_PARALLEL_LIMIT=4 "${COMPOSE[@]}" up -d --remove-orphans
	# The Caddyfile arrives through a bind mount (of its directory — a file mount follows the
	# inode and keeps serving the pre-checkout file), so editing it changes nothing compose
	# can see: `up` leaves the container, and the config it loaded at start, exactly as it
	# was. A deploy that only touched the Caddyfile used to deploy nothing. `caddy reload` is
	# graceful: the new config is validated, then swapped in with no dropped connection, so
	# unlike the recreate above this step costs no downtime.
	log "reloading Caddy with the Caddyfile from this ref (graceful, no downtime)"
	# If this deploy recreated Caddy itself, it may not accept an exec for a moment; one failed
	# exec must not abort the deploy before `health` and `record_ref` run.
	local try
	for try in 1 2 3 4 5; do
		"${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile && return 0
		sleep 2
	done
	echo "caddy reload did not succeed after 5 tries" >&2
	return 1
}

health() {
	log "health"
	# start_period 90 s + three retries at 60 s is 270 s of legitimately "starting" per container,
	# and containers start four at a time. 120 s reported a healthy box as a failed deploy, which
	# skipped record_ref — so the *next* rollback went one ref too far back.
	local deadline=$((SECONDS + ${HEALTH_TIMEOUT:-600}))
	local unhealthy expected missing
	# Compare against what this box is configured to run. `ps --all` (not the default `ps`,
	# which hides exited containers entirely) is what "has a container at all" means here —
	# the default form used to also count a legitimately-exited one-shot as "missing" on top
	# of the (correct) unhealthy report below, which happened to cancel out only because every
	# non-running container used to be treated as a failure regardless.
	expected=$("${COMPOSE[@]}" config --services | sort)
	while [ $SECONDS -lt $deadline ]; do
		unhealthy=$("${COMPOSE[@]}" ps --all --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}|{{.Labels}}' |
			unhealthy_from_ps | tr '\n' ' ')
		missing=$(comm -23 <(echo "$expected") <("${COMPOSE[@]}" ps --all --format '{{.Service}}' | sort -u) | tr '\n' ' ')
		[ -z "$missing" ] || unhealthy="$unhealthy missing:$missing"
		if [ -z "${unhealthy// /}" ]; then
			"${COMPOSE[@]}" ps
			echo "health: every container is up"
			return 0
		fi
		sleep 5
	done
	"${COMPOSE[@]}" ps
	echo "health: still not settled after ${HEALTH_TIMEOUT:-600}s: $unhealthy" >&2
	local name
	for name in $unhealthy; do
		name=${name%%(*}
		case "$name" in missing:* | "") continue ;; esac
		echo "--- last lines of $name" >&2
		"${COMPOSE[@]}" logs --tail 20 "$name" >&2 || true
	done
	echo "  The deploy is NOT rolled back automatically. Read the lines above first: a rollback" >&2
	echo "  recreates every container on a box that is already struggling. RUNBOOK.md," >&2
	echo "  'roll back a bad deploy'." >&2
	return 1
}

# New permission codes reach a brand-new organization the moment it activates a product, and an
# organization that ALREADY activated it never — until this runs (ADR-0007). It is a fleet write:
# every organization's roles are widened (union only, nothing removed) and its permission
# version bumped, which makes every signed-in member refresh their token. So it is never part of
# `deploy`; it is a step somebody decides to take, and the dry run is always printed first.
#
# Runs in a throwaway container from the account worker's definition, because that is the one
# that already has the account code, the permission manifests mounted and Redis in reach.
reconcile_permissions() {
	local products="$1" mode="${2:-}"
	[ -n "$products" ] || { echo "usage: remote.sh reconcile-permissions <product[,product…]> [--apply]" >&2; exit 64; }
	local product
	for product in $(echo "$products" | tr ',' ' '); do
		case "$product" in *[!a-z_]*) echo "refusing product key '$product'" >&2; exit 64 ;; esac
		log "reconcile permissions: $product (dry run)"
		_reconcile_run "$product" --dry-run
		if [ "$mode" = "--apply" ]; then
			log "reconcile permissions: $product (applying)"
			_reconcile_run "$product"
		fi
	done
	[ "$mode" = "--apply" ] || echo "dry run only — nothing was written. Re-run with --apply to write it."
}

_reconcile_run() {
	local product="$1"
	shift
	"${COMPOSE[@]}" run --rm --no-deps -T \
		-e PYTHONPATH=/app \
		-v "$(cd ../.. && pwd)/tools:/opt/tools:ro" \
		account-worker python /opt/tools/reconcile_permissions.py "$product" --all "$@"
}

record_ref() {
	local sha
	sha=$(git -C ../.. rev-parse HEAD)
	mkdir -p "$STATE_DIR"
	# Written only after health passes, so `last-good-ref` is a ref that actually served, and
	# `deploy-log` is the list of refs that actually served — which is what a rollback needs.
	echo "$sha" >"$STATE_DIR/last-good-ref"
	printf '%s %s\n' "$(date -u +%FT%TZ)" "$sha" >>"$STATE_DIR/deploy-log"
	echo "recorded good ref: $sha"
}

# The ref to roll back TO: the most recent one that served before the one serving now.
previous_ref() {
	local prev
	# `|| true`: with `set -e` and `pipefail`, a missing deploy-log would otherwise abort here
	# with awk's exit status and no explanation — which is the opposite of the point.
	prev=$(awk '{print $2}' "$STATE_DIR/deploy-log" 2>/dev/null | awk '!seen[$0]++' | tail -2 | head -1 || true)
	if [ -z "$prev" ] || [ "$prev" = "$(cat "$STATE_DIR/last-good-ref" 2>/dev/null)" ]; then
		echo "no earlier successful deploy is recorded on this box." >&2
		echo "  Find the previous commit yourself (git log on the box) and pass it:" >&2
		echo "  make edge-rollback HOST=... CONFIRM=... TO=<sha>" >&2
		exit 1
	fi
	echo "$prev"
}

case "${1:-}" in
assert-host) assert_host "${2:-}" ;;
preflight) ./preflight.sh ;;
build) build ;;
validate) validate ;;
migrate) migrate ;;
up) up ;;
health) health ;;
deploy)
	echo "deploying $(git -C ../.. rev-parse --short HEAD) to $(hostname) at $(date -u +%FT%TZ)"
	./preflight.sh
	build
	validate
	migrate
	up
	health
	record_ref
	;;
previous-ref) previous_ref ;;
last-good-ref) good_ref ;;
reconcile-permissions) reconcile_permissions "${2:-}" "${3:-}" ;;
backup) ./backup.sh ;;
status)
	"${COMPOSE[@]}" ps
	echo
	echo "last good ref: $(cat "$STATE_DIR/last-good-ref" 2>/dev/null || echo unknown)"
	echo "checked out:   $(git -C ../.. rev-parse HEAD)"
	echo
	df -h .
	;;
logs)
	# `logs [lines] [service...]`. Taken as arguments rather than read from the environment:
	# ssh does not forward variables, so a TAIL= that only existed on the caller's side would
	# be silently ignored.
	shift
	lines="${1:-200}"
	# An `&& shift` here would exit the script under `set -e` when no service was named.
	if [ $# -gt 0 ]; then shift; fi
	"${COMPOSE[@]}" logs --tail "$lines" "$@"
	;;
compose)
	shift
	"${COMPOSE[@]}" "$@"
	;;
*)
	echo "usage: remote.sh {assert-host <host>|preflight|build|validate|migrate|up|health|deploy|previous-ref|backup|status|logs [lines] [svc]|compose ...}" >&2
	exit 64
	;;
esac
