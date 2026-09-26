#!/usr/bin/env bash
# Start / stop the minimum backend the textile sales demo needs, on the committed gateway route
# table's own ports (services/app-gateway/routes.json), against whatever DATABASE_URL the root
# `.env` names — which for the demo must be `opsgrids_demo` (see docs/demo/textile-demo.md).
#
#   tools/demo/demo-stack.sh up       # auth, account, product, operations, finance, marketing,
#                                     # notification, then app-gateway on :8100
#   tools/demo/demo-stack.sh status
#   tools/demo/demo-stack.sh down
#
# Logs go to $DEMO_LOG_DIR (default /tmp/opsgrids-demo). No relays, consumers or cron run:
# events land in each service's outbox and stay there, which is what a demo without RabbitMQ
# consumers wants — nothing is sent anywhere. Refuses to start if `.env` points at the shared
# `opsgrids` database.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

LOG_DIR="${DEMO_LOG_DIR:-/tmp/opsgrids-demo}"
mkdir -p "$LOG_DIR"

# name:port — the ports are routes.json's; the gateway goes last.
SERVICES=(auth:8110 account:8111 product:8112 marketing:8134 notification:8151 finance:8171 operations:8172 app-gateway:8100)

db_name() {
	sed -n 's#^DATABASE_URL=.*/\([A-Za-z0-9_]*\)$#\1#p' .env | head -1
}

case "${1:-}" in
up)
	[ -f .env ] || { echo "no .env — copy .env.example and point DATABASE_URL at opsgrids_demo" >&2; exit 1; }
	db="$(db_name)"
	if [ "$db" = "opsgrids" ] || [ -z "$db" ]; then
		echo "refusing: .env DATABASE_URL names '${db:-?}', not a demo database" >&2
		exit 1
	fi
	for entry in "${SERVICES[@]}"; do
		name="${entry%%:*}"
		port="${entry##*:}"
		# Never adopt a stack somebody else started: it may be wired to another database.
		if lsof -ti "tcp:$port" -sTCP:LISTEN >/dev/null 2>&1; then
			echo "refusing: :$port ($name) is already in use — stop that process first" >&2
			exit 1
		fi
		(cd "services/$name" && nohup uv run uvicorn app.main:app --host 127.0.0.1 --port "$port" \
			>"$LOG_DIR/$name.log" 2>&1 &
			echo $! >"$LOG_DIR/$name.pid")
		echo "started $name on :$port (log $LOG_DIR/$name.log)"
	done
	;;
status)
	for entry in "${SERVICES[@]}"; do
		name="${entry%%:*}"
		port="${entry##*:}"
		code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health" || true)"
		echo "$name :$port -> $code"
	done
	;;
down)
	for entry in "${SERVICES[@]}"; do
		name="${entry%%:*}"
		port="${entry##*:}"
		pidfile="$LOG_DIR/$name.pid"
		[ -f "$pidfile" ] || continue
		# `uv run` execs the server in the same process on macOS/Linux, so this pid is the server.
		kill "$(cat "$pidfile")" 2>/dev/null && echo "stopped $name (:$port)" || true
		rm -f "$pidfile"
	done
	;;
*)
	echo "usage: $0 up|status|down" >&2
	exit 2
	;;
esac
