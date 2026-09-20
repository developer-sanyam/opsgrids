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

COMPOSE=(docker compose -f compose.prod.yml --env-file .env)
STATE_DIR="${EDGE_STATE_DIR:-/srv/opsgrids/state}"
SERVICES_WITH_SCHEMA=(auth account product storefront)

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

build() {
	log "building images (running containers are untouched until 'up')"
	"${COMPOSE[@]}" build --pull
}

validate() {
	log "validating the Caddyfile against the pinned Caddy image"
	# --no-deps so this cannot start anything, -T because there is no terminal over ssh.
	"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint caddy caddy \
		validate --config /etc/caddy/Caddyfile --adapter caddyfile
}

migrate() {
	log "migrations (before the new version serves)"
	for svc in "${SERVICES_WITH_SCHEMA[@]}"; do
		echo "--- $svc"
		# Each image carries its own alembic.ini and migrations/, and env.py connects as the
		# admin role. No foreign key crosses a schema, so the order between services does not
		# matter; they are run one at a time because this box has four vCPU and is serving.
		"${COMPOSE[@]}" run --rm -T "$svc" alembic upgrade head
	done
}

up() {
	log "recreating containers (brief downtime for anything that changed)"
	"${COMPOSE[@]}" up -d --remove-orphans
	# The Caddyfile arrives through a bind mount (of its directory — a file mount follows the
	# inode and keeps serving the pre-checkout file), so editing it changes nothing compose
	# can see: `up` leaves the container, and the config it loaded at start, exactly as it
	# was. A deploy that only touched the Caddyfile used to deploy nothing. `caddy reload` is
	# graceful: the new config is validated, then swapped in with no dropped connection, so
	# unlike the recreate above this step costs no downtime.
	log "reloading Caddy with the Caddyfile from this ref (graceful, no downtime)"
	"${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
}

health() {
	log "health"
	local deadline=$((SECONDS + 120))
	local unhealthy
	while [ $SECONDS -lt $deadline ]; do
		unhealthy=$("${COMPOSE[@]}" ps --format '{{.Service}} {{.State}} {{.Health}}' |
			awk '$2 != "running" || ($3 != "" && $3 != "healthy" && $3 != "<nil>") {print $1"("$2"/"$3")"}' |
			tr '\n' ' ')
		if [ -z "$unhealthy" ]; then
			"${COMPOSE[@]}" ps
			echo "health: every container is up"
			return 0
		fi
		sleep 5
	done
	"${COMPOSE[@]}" ps
	echo "health: still not settled after 120s: $unhealthy" >&2
	echo "  The deploy is NOT rolled back automatically. Decide from RUNBOOK.md," >&2
	echo "  'a deploy went bad' — the usual answer is: make edge-rollback." >&2
	return 1
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
