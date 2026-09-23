#!/usr/bin/env bash
# Everything that must be true on the box before a deploy is allowed to change anything.
# Runs on the VM, called by remote.sh. Exits non-zero and says which row failed.
#
# The rule it exists to enforce: every key in `.env.example` is present in the box's `.env`
# with a real value. A missing key does not fail loudly on its own — pydantic has a default
# for most of them, and the default is the development value — so the deploy is where it has
# to be caught.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

ok() { printf '  ok    %s\n' "$*"; }
bad() {
	printf '  FAIL  %s\n' "$*" >&2
	failed=1
}
failed=0

echo "preflight: $(hostname) $(date -u +%FT%TZ)"

# ---------------------------------------------------------------- the environment file
if [ ! -f .env ]; then
	echo "  FAIL  infrastructure/edge/.env does not exist on this box." >&2
	echo "        Copy .env.example to .env and fill it in. See RUNBOOK.md, 'first deploy'." >&2
	exit 1
fi

mode=$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env)
case "$mode" in
600 | 400) ok ".env permissions ($mode)" ;;
*) bad ".env is mode $mode — it holds every production secret. chmod 600 infrastructure/edge/.env" ;;
esac

# Key names come from the committed example, so adding a key there is what makes it required.
# Values are never printed — only names.
required=$(sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env.example)
# Keys that are allowed to be empty, each for a stated reason. The two backup credentials are
# allowed empty *together* and warned about loudly below: a box can be stood up before an
# off-box bucket exists, and `backup.sh` still takes the local dump and exits non-zero until
# one is configured — so the gap shows up in cron's mail every night rather than nowhere.
optional_empty="PII_DECRYPT_KEYS SENTRY_DSN BACKUP_S3_ENDPOINT BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY"
# Email and payments may be unconfigured on a box that is being stood up: the services are
# inert without them (notification logs, payment reaches no network) and each is warned about
# below. WhatsApp is optional until a Meta app exists. COMPOSE_PROFILES empty means core only.
# OPS_ALERT_EMAIL is the same shape: ops-monitor still runs and still logs `ops.alert.firing`
# with nothing configured, it just pages nobody — warned about below, not refused.
optional_empty="$optional_empty SMTP_HOST SMTP_USERNAME SMTP_PASSWORD RAZORPAY_KEY_ID RAZORPAY_KEY_SECRET OPS_ALERT_EMAIL"
# Empty means "check only the always-on core" (compose.prod.yml's own default for each) — a
# perfectly valid state on a box that sells nothing beyond core, not a placeholder to fill in.
optional_empty="$optional_empty OPS_MONITOR_API_CONTAINERS OPS_MONITOR_DLQ_QUEUES"
optional_empty="$optional_empty WHATSAPP_APP_SECRET WHATSAPP_VERIFY_TOKEN WHATSAPP_PHONE_NUMBER_ID WHATSAPP_ACCESS_TOKEN COMPOSE_PROFILES"
# A box with no floating IP yet (or a provider that does not offer one) has nothing to put here;
# storefront's own settings default it to empty and print "the platform address" instead of a
# literal until it is filled in. Checked separately, below, for a documentation-range value.
optional_empty="$optional_empty EDGE_RESERVED_IP"
# The orphan sweep's own credential (ADR-0009 §9 addendum) is off by default
# (DOCUMENT_ORPHAN_SWEEP_ENABLED=false) — a box selling Storefront works fully without it, and
# generating a MinIO user for a pass that is not yet turned on is a step an operator should
# take deliberately, not one preflight forces on every box the day this shipped.
optional_empty="$optional_empty DOCUMENT_S3_ORPHAN_ACCESS_KEY DOCUMENT_S3_ORPHAN_SECRET_KEY"

set -a
# shellcheck disable=SC1091 # .env is data, generated on the box from .env.example
. ./.env
set +a

missing=""
placeholder=""
for key in $required; do
	value="${!key-__unset__}"
	if [ "$value" = "__unset__" ]; then
		missing="$missing $key"
		continue
	fi
	if [ -z "$value" ]; then
		case " $optional_empty " in
		*" $key "*) continue ;;
		*)
			missing="$missing $key"
			continue
			;;
		esac
	fi
	case "$value" in
	replace-me* | CHANGE_ME* | dev-only-* | ci-only-* | *example.com*)
		# `ops@example.com` is the committed ACME placeholder; a real deploy needs an inbox a
		# human reads, because in slice 1 Let's Encrypt's expiry email is the only notification
		# that exists.
		placeholder="$placeholder $key"
		;;
	esac
done

if [ -z "$missing" ]; then ok "every key in .env.example is set"; else
	bad "missing or empty in .env:$missing"; fi
if [ -z "$placeholder" ]; then ok "no placeholder values"; else
	bad "still carrying a placeholder value:$placeholder"; fi

# EDGE_RESERVED_IP is allowed empty (a box with no floating IP yet), but if it is set it must be
# a real address: an address in one of RFC 5737's three documentation ranges (192.0.2.0/24,
# 198.51.100.0/24, 203.0.113.0/24 — the ADR's own worked examples use 198.51.100.7) reaches
# nobody, and a merchant who copies it into their apex A record gets a shop that never resolves.
# A warning, not a hard failure: a box can be legitimately mid-setup with a value someone typed
# by hand and has not finished checking yet.
case "${EDGE_RESERVED_IP:-}" in
"") ;;
192.0.2.* | 198.51.100.* | 203.0.113.*)
	echo "  warn  EDGE_RESERVED_IP ($EDGE_RESERVED_IP) is an RFC 5737 documentation address —" >&2
	echo "        it reaches nobody. Set the box's real reserved IP, or leave it empty." >&2
	;;
*) ok "EDGE_RESERVED_IP is not a documentation-range address" ;;
esac

if [ -z "${BACKUP_S3_ACCESS_KEY_ID:-}" ] && [ -z "${BACKUP_S3_SECRET_ACCESS_KEY:-}" ]; then
	echo "  warn  BACKUP_S3_ACCESS_KEY_ID/SECRET are empty: NO OFF-BOX BACKUP. The nightly dump" >&2
	echo "        stays on this disk, next to the database it backs up. Configure a bucket" >&2
	echo "        (RUNBOOK.md, 'restore from backup') before there is data worth keeping." >&2
elif [ -z "${BACKUP_S3_ACCESS_KEY_ID:-}" ] || [ -z "${BACKUP_S3_SECRET_ACCESS_KEY:-}" ]; then
	bad "one of BACKUP_S3_ACCESS_KEY_ID / BACKUP_S3_SECRET_ACCESS_KEY is set and the other is empty"
else
	ok "off-box backup credentials present"
fi

# ---------------------------------------------------------------- object storage (ADR-0009)
# MinIO enforces an 8-character minimum on both its root password and any user it creates.
# Failing here, before minio-init ever runs, turns a cryptic MinIO startup/user-creation error
# into a preflight message that names the key — never the value.
for key in MINIO_ROOT_PASSWORD DOCUMENT_S3_SECRET_KEY; do
	value="${!key:-}"
	if [ -n "$value" ] && [ "${#value}" -lt 8 ]; then
		bad "$key is shorter than MinIO's 8-character minimum"
	else
		ok "$key length"
	fi
done

# PUBLIC_ASSET_BASE_URL must resolve to exactly the one host `caddy/Caddyfile` proxies
# `/opsgrids-public/*` on — EDGE_HOST, and only EDGE_HOST (see the Caddyfile's "uploaded
# merchant images" section: the route was deliberately narrowed off the shared snippet every
# other platform hostname imports, to close a stored-XSS path through MinIO's
# `?response-content-type=` override on a host carrying the `.opsgrids.com`-scoped refresh
# cookie). Pointing it at a different host — even one correctly listed in TLS_PLATFORM_HOSTS —
# builds a URL Caddy now answers 404 on, for a reason nobody will guess from the symptom.
expected_asset_base="https://${EDGE_HOST:-}/${S3_PUBLIC_BUCKET:-opsgrids-public}"
case "${PUBLIC_ASSET_BASE_URL:-}" in
"$expected_asset_base") ok "PUBLIC_ASSET_BASE_URL matches https://\$EDGE_HOST/\$S3_PUBLIC_BUCKET" ;;
*) bad "PUBLIC_ASSET_BASE_URL must be exactly https://\$EDGE_HOST/\$S3_PUBLIC_BUCKET (names only: currently pointed somewhere that is not this box's own EDGE_HOST, or at the wrong bucket path)" ;;
esac

# ---------------------------------------------------------------- the signing key
if [ -n "${JWT_PRIVATE_KEY_HOST_PATH:-}" ] && [ -f "$JWT_PRIVATE_KEY_HOST_PATH" ]; then
	keymode=$(stat -c '%a' "$JWT_PRIVATE_KEY_HOST_PATH" 2>/dev/null || stat -f '%Lp' "$JWT_PRIVATE_KEY_HOST_PATH")
	case "$keymode" in
	400 | 440 | 600 | 640) ok "auth signing key present (mode $keymode)" ;;
	*) bad "signing key $JWT_PRIVATE_KEY_HOST_PATH is mode $keymode — it is the only private key in the platform" ;;
	esac
else
	bad "no signing key at ${JWT_PRIVATE_KEY_HOST_PATH:-<unset>} — auth-service will not start"
fi

# ---------------------------------------------------------------- the box itself
free_kb=$(df -Pk . | awk 'NR==2 {print $4}')
free_gb=$((free_kb / 1024 / 1024))
if [ "$free_gb" -lt 5 ]; then
	bad "only ${free_gb}G free — a build needs several and a full disk stops Postgres writing. See RUNBOOK.md, 'the box is out of disk'"
elif [ "$free_gb" -lt 15 ]; then
	ok "${free_gb}G free (low — run: docker image prune -af && docker builder prune -af)"
else
	ok "${free_gb}G free"
fi

inodes=$(df -Pi . | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
if [ "${inodes:-0}" -lt 90 ]; then ok "inodes ${inodes}% used"; else bad "inodes ${inodes}% used"; fi

if docker info >/dev/null 2>&1; then ok "docker daemon responding"; else bad "docker daemon not responding"; fi

# ---------------------------------------------------------------- the memory budget
# ADR-0013's memory-budget erratum (2026-09-23): the box does not grow to meet the container
# count; the container count is bounded to what the box actually has. `memory-budget.json` is
# measured, not guessed (see its own header), and this is the same arithmetic
# `tests/infra/test_compose_memory_budget.py` pins against `compose.prod.yml` — a service added
# to one and not the other fails there first, in CI, rather than surprising anyone here.
budget_file="memory-budget.json"
if [ ! -f "$budget_file" ]; then
	echo "  warn  no $budget_file — memory budget check skipped" >&2
elif ! command -v python3 >/dev/null 2>&1; then
	echo "  warn  no python3 on this box — memory budget check skipped" >&2
else
	expected_mb=$(python3 lib/memory_budget_totals.py "$budget_file" "${COMPOSE_PROFILES:-}") || {
		bad "could not compute the expected memory budget from $budget_file for COMPOSE_PROFILES=${COMPOSE_PROFILES:-}"
		expected_mb=""
	}
	if [ -n "$expected_mb" ]; then
		mem_total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo "")
		if [ -n "$mem_total_kb" ]; then
			# shellcheck disable=SC1091  # sourced, not executed — see the file's own header
			. ./lib/memory-budget.sh
			verdict_line=$(memory_budget_verdict "$mem_total_kb" "$expected_mb" 80 100)
			verdict=${verdict_line%% *}
			percent=${verdict_line#* }
			mem_total_mb=$((mem_total_kb / 1024))
			case "$verdict" in
			ok) ok "memory budget: ${expected_mb}M expected of ${mem_total_mb}M MemTotal (${percent}%)" ;;
			warn)
				echo "  warn  memory budget: ${expected_mb}M expected of ${mem_total_mb}M MemTotal (${percent}%)" >&2
				echo "        — over the 80% soft line. Still deploying; see RUNBOOK.md, 'the memory budget'." >&2
				;;
			refuse)
				bad "memory budget: ${expected_mb}M expected of ${mem_total_mb}M MemTotal (${percent}%) — over the hard budget. Trim COMPOSE_PROFILES or consolidate a service before deploying (ADR-0013's memory-budget erratum)."
				;;
			esac
		else
			echo "  warn  could not read /proc/meminfo — memory budget check skipped" >&2
		fi
	fi
fi

# ---------------------------------------------------------------- what this box sells
if [ "${EMAIL_TRANSPORT:-log}" = "log" ]; then
	echo "  warn  EMAIL_TRANSPORT=log: NO EMAIL IS SENT. A signup cannot set a password and a" >&2
	echo "        trial cannot be warned. Set EMAIL_TRANSPORT=smtp and the SMTP_* values." >&2
else
	ok "email transport: $EMAIL_TRANSPORT"
fi
if [ -z "${RAZORPAY_KEY_ID:-}" ]; then
	echo "  warn  RAZORPAY_KEY_ID empty: payments are inert. Trials end in past_due, never active." >&2
else
	ok "payment provider keys present"
fi

# ops-monitor (C10) always runs and always checks; this is only about whether a firing check
# reaches a human. Without both, it still does the right thing — `ops.alert.firing` at error
# in the logs, and `/status` says "disabled" in so many words — but nobody not already
# watching logs finds out. Same shape as the EMAIL_TRANSPORT warning above: loud, not fatal.
if [ -z "${OPS_ALERT_EMAIL:-}" ] || [ -z "${SMTP_HOST:-}" ]; then
	echo "  warn  OPS_ALERT_EMAIL/SMTP_HOST not fully set: ops-monitor logs alerts but sends" >&2
	echo "        NONE. Nobody is paged. Set OPS_ALERT_EMAIL and the SMTP_* values — see" >&2
	echo "        RUNBOOK.md, 'alerts and what each means'." >&2
else
	ok "ops-monitor alerts email: $OPS_ALERT_EMAIL"
fi

# The send gate itself refuses a campaign outside local/test/ci while this is still
# localhost/127.0.0.1 or plain http:// (services/notification/app/services.py's
# `_check_public_base`) — this just catches it before a deploy instead of at the first send.
case "${ENGAGE_PUBLIC_API_BASE:-}" in
https://*localhost* | https://*127.0.0.1*)
	bad "ENGAGE_PUBLIC_API_BASE ($ENGAGE_PUBLIC_API_BASE) is not reachable outside this box — every campaign send will be refused"
	;;
https://*) ok "engage public API base: $ENGAGE_PUBLIC_API_BASE" ;;
*) bad "ENGAGE_PUBLIC_API_BASE ($ENGAGE_PUBLIC_API_BASE) must be https — every campaign send will be refused" ;;
esac

# Every hostname the Caddyfile serves needs a certificate, and the issuance gate
# (storefront's tls-authorize) admits a platform name only if TLS_PLATFORM_HOSTS lists it. A
# site added to the Caddyfile and not to the box's .env serves a TLS error, not a page.
while read -r site; do
	case ",${TLS_PLATFORM_HOSTS:-}," in
	*",$site,"*) ;;
	*) bad "the Caddyfile serves $site but TLS_PLATFORM_HOSTS does not list it: no certificate will be issued" ;;
	esac
# Process substitution, not a pipe: `bad` must set `failed` in this shell, not in a subshell.
done < <(sed -n 's#^https://\([a-z0-9.-]*\) {$#\1#p' caddy/Caddyfile)

# A prefix in the route table whose upstream is not among this box's services is a hung
# connection and a 504 for whoever calls it. Product routes are covered by the catalogue check
# below; this catches the ones with no product key (approvals runs only with the hrms profile).
running_services=" $(docker compose -f compose.prod.yml --env-file .env config --services 2>/dev/null | tr '\n' ' ') "
while read -r upstream; do
	case "$running_services" in
	*" $upstream "*) ;;
	*) echo "  warn  routes.prod.json sends traffic to '$upstream', which this box's profiles do not run (callers get 504)" >&2 ;;
	esac
done < <(grep -o '"upstream": *"http://[a-z-]*' routes.prod.json | sed 's#.*http://##' | sort -u)

# Same check for the Caddyfile's own reverse proxies (the gateway aside, `minio` is the one
# that is profile-gated today): a store's uploaded images 502 through Caddy rather than 404
# from the gateway, which is a worse symptom to debug cold, so it gets named here too.
while read -r upstream; do
	case "$running_services" in
	*" $upstream "*) ;;
	*) echo "  warn  caddy/Caddyfile reverse-proxies to '$upstream', which this box's profiles do not run (that path 502s)" >&2 ;;
	esac
done < <(grep -oE 'reverse_proxy [a-z-]+:[0-9]+' caddy/Caddyfile | awk '{print $2}' | cut -d: -f1 | sort -u)

# The catalogue says which products are sold; COMPOSE_PROFILES says which product services
# run. They must agree (ADR-0013 §5): an `available` product with no service behind it is a
# 504 the customer finds. Only checkable once Postgres exists — on the first deploy it does
# not, and the row comes from a migration that has not run yet.
profile_for() {
	case "$1" in
	operations) echo inventory ;;
	storefront) echo storefront ;;
	hrms|crm|pulseboard|projects|support|marketing|finance|itsm) echo "$1" ;;
	*) echo "" ;;
	esac
}
if docker compose -f compose.prod.yml --env-file .env ps --status running postgres 2>/dev/null | grep -q postgres; then
	catalogue_rc=0
	available=$(docker compose -f compose.prod.yml --env-file .env exec -T postgres \
		psql -U opsgrids -d opsgrids -Atc "select key from product.products where status = 'available' and not is_platform" 2>/dev/null) || catalogue_rc=$?
	# An empty answer and a failed question look the same to the loop below, and a green line
	# for a check that never ran is worse than no check.
	[ "$catalogue_rc" -eq 0 ] || bad "could not read the product catalogue (psql exit $catalogue_rc): the catalogue/profile agreement is unverified"
	enabled=",${COMPOSE_PROFILES:-},"
	sold_unserved=""
	for key in $available; do
		profile=$(profile_for "$key")
		[ -n "$profile" ] || continue
		case "$enabled" in
		*",$profile,"*) ;;
		*) sold_unserved="$sold_unserved $key(profile:$profile)" ;;
		esac
	done
	if [ -z "$sold_unserved" ]; then ok "every product the catalogue sells has its service profile enabled"; else
		bad "sold in the catalogue but no service will run for it:$sold_unserved — add the profile to COMPOSE_PROFILES or set the product to 'soon'"; fi
	for profile in $(echo "${COMPOSE_PROFILES:-}" | tr ',' ' '); do
		key=""
		case "$profile" in inventory) key=operations ;; storefront) key=storefront ;; hrms|crm|pulseboard|projects|support|marketing|finance|itsm) key=$profile ;; esac
		[ -n "$key" ] || continue
		case " $available " in
		*" $key "*) ;;
		*) echo "  warn  profile '$profile' runs but the catalogue does not sell '$key' yet (status is not 'available')" >&2 ;;
		esac
	done
else
	echo "  warn  Postgres not running yet: the catalogue/profile agreement check is skipped on this deploy" >&2
fi

# ---------------------------------------------------------------- the web root
web_root="${EDGE_WEB_ROOT:-/srv/opsgrids/web}"
if [ -e "$web_root/current/index.html" ]; then
	ok "buyer bundle published ($(readlink -f "$web_root/current" 2>/dev/null || echo "$web_root/current"))"
else
	# Not fatal. The API half of the box is useful on its own, and the edge answers with a
	# page that says what is true rather than with a merchant's store.
	echo "  warn  no buyer bundle at $web_root/current — every storefront host will answer" >&2
	echo "        the edge's 'not serving a store' page. Publish with: make edge-web HOST=..." >&2
fi

if [ "$failed" -ne 0 ]; then
	echo "preflight: FAILED — nothing was changed." >&2
	exit 1
fi
echo "preflight: ok"
