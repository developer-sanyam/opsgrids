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
optional_empty="$optional_empty SMTP_HOST SMTP_USERNAME SMTP_PASSWORD RAZORPAY_KEY_ID RAZORPAY_KEY_SECRET"
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
# EDGE_PROFILES is an explicit, optional override for COMPOSE_PROFILES (see the note below) —
# most boxes never set it; COMPOSE_PROFILES alone is the common case.
optional_empty="$optional_empty EDGE_PROFILES"
# EDGE_BUDGET_ACCEPTED_MB (B2, review round 2) — the owner's explicit memory-overcommit ceiling;
# most boxes never need one at all (see the memory budget section, below).
optional_empty="$optional_empty EDGE_BUDGET_ACCEPTED_MB"
# M5 (review round 2): these two keys may be entirely ABSENT from an existing box's .env, not
# merely empty — `EDGE_PROFILES`/`EDGE_BUDGET_ACCEPTED_MB` did not exist when older boxes were
# first stood up, and re-copying the whole of `.env.example` onto a live box just to add two
# lines is exactly the kind of manual step this file exists to make unnecessary. Every other key
# in `.env.example` staying required-present is deliberate: an absent key silently reverting to
# whatever default `platform_core.config.ServiceSettings` happens to ship is the same class of
# bug COMPOSE_PROFILES being merely empty already guards against, one layer further back.
optional_absent="EDGE_PROFILES EDGE_BUDGET_ACCEPTED_MB"

set -a
# shellcheck disable=SC1091 # .env is data, generated on the box from .env.example
. ./.env
set +a

# ---------------------------------------------------------------- which profiles this box declares
# EDGE_PROFILES, if set, is an explicit, authoritative override; COMPOSE_PROFILES otherwise —
# `remote.sh` applies the identical precedence before invoking docker compose, so `declared_
# profiles` below is what will actually be asked to run, not a guess.
#
# **Nothing here computes this value automatically any more (H2, the memory-headroom slice's
# 2026-09-23 fix round).** The previous "zero-activation profile auto-off" fell back to
# computing COMPOSE_PROFILES from the catalogue's own activations when both were unset — but
# `docker compose up` does not stop a container whose profile was silently removed (an "orphan"
# in compose's own vocabulary is a service no longer in the file at all, not one merely filtered
# out by profile), so that computed value never actually changed what was running — only what
# this script *believed* was running, which is a worse failure mode than doing nothing. A human
# decides what runs and writes it down in `EDGE_PROFILES`/`COMPOSE_PROFILES`; the checks below
# only ever advise about that decision, never make it.
# shellcheck source=lib/active-profiles.sh
. ./lib/active-profiles.sh
declared_profiles=$(resolve_declared_profiles "${EDGE_PROFILES:-}" "${COMPOSE_PROFILES:-}")
if [ -n "${EDGE_PROFILES:-}" ]; then
	ok "COMPOSE_PROFILES overridden by EDGE_PROFILES: $declared_profiles"
fi
# Exported so every `docker compose` call below (and remote.sh's own, later) resolves the same
# profile list this script just did — an exported shell variable takes precedence over the value
# `--env-file .env` would otherwise read, which is what makes EDGE_PROFILES actually override
# COMPOSE_PROFILES for compose itself, not just for this script's own arithmetic.
export COMPOSE_PROFILES="$declared_profiles"

missing=""
placeholder=""
for key in $required; do
	value="${!key-__unset__}"
	if [ "$value" = "__unset__" ]; then
		case " $optional_absent " in
		*" $key "*) continue ;;
		*)
			missing="$missing $key"
			continue
			;;
		esac
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
	# B1 (review round 2): price the box this deploy is about to leave behind, not the one it
	# found. `docker compose up` starts what a newly-enabled profile adds; it never tears down
	# what a shrunk one no longer lists (H2). Pricing only what is *running* today would miss a
	# profile this deploy is about to turn on; pricing only the *declared* profile list would
	# miss an orphan a previous, incomplete change left behind. `post_deploy_compute`
	# (lib/memory-budget.sh — the same function remote.sh's `record_budget` calls, so what a deploy
	# is judged against and what gets recorded afterwards can never be priced two ways) takes the
	# union of both: containers running today that the new compose file still defines, plus
	# whatever the declared profiles configure. See lib/memory_budget_totals.py's
	# `post_deploy_container_names` for the full argument and tests/infra/
	# test_memory_budget_totals_containers.py::TestPostDeployContainerNames for the cases.
	# shellcheck disable=SC1091  # sourced, not executed — see the file's own header
	. ./lib/memory-budget.sh
	expected_mb=""
	if post_deploy_compute "$budget_file"; then
		expected_mb="$POST_DEPLOY_MB"
	else
		bad "could not compute the post-deploy expected memory budget from $budget_file"
	fi
	priced_as="post-deploy: running that the new compose still defines, plus declared profiles"

	# M7: an unpriced but definitely-ours container (the label filter already scopes `docker ps`
	# to this compose project) is a gap in memory-budget.json, not a foreign container to ignore
	# — named here so it gets fixed; priced conservatively above (kinds.background) rather than
	# as free.
	if [ -n "${POST_DEPLOY_UNPRICED:-}" ]; then
		echo "  warn  container(s) with no memory-budget.json entry, priced as an estimate (kinds.background): ${POST_DEPLOY_UNPRICED} — add them to memory-budget.json's 'containers'." >&2
	fi
	# A ceiling that is not a plain whole number of MB is a typo, not a decision — said out loud
	# rather than silently ignored (it is treated as unset below, so the deploy would refuse).
	case "${EDGE_BUDGET_ACCEPTED_MB:-}" in
	*[!0-9]*) bad "EDGE_BUDGET_ACCEPTED_MB (${EDGE_BUDGET_ACCEPTED_MB}) must be a whole number of megabytes" ;;
	esac

	# Read from the budget file itself rather than hardcoded here — this file's own
	# `host.soft_budget_percent`/`hard_budget_percent` is what `tests/infra/
	# test_compose_memory_budget.py` pins, and a hardcoded copy here would silently drift from
	# it the next time either threshold changes.
	soft_percent=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['host']['soft_budget_percent'])" "$budget_file")
	hard_percent=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['host']['hard_budget_percent'])" "$budget_file")
	if [ -n "$expected_mb" ]; then
		mem_total_kb=$(awk '/^MemTotal:/ {print $2}' "${EDGE_MEMINFO_FILE:-/proc/meminfo}" 2>/dev/null || echo "")
		if [ -n "$mem_total_kb" ]; then
			verdict_line=$(memory_budget_verdict "$mem_total_kb" "$expected_mb" "$soft_percent" "$hard_percent")
			verdict=${verdict_line%% *}
			percent=${verdict_line#* }
			mem_total_mb=$((mem_total_kb / 1024))

			# B2/H3/H4 (review round 2): an explicit owner ceiling replaces the previous
			# grandfather-vs-last-ok ratchet. `EDGE_BUDGET_ACCEPTED_MB` in `.env`, if set, is the
			# total (MB) the owner has actually looked at the projection and agreed to run at —
			# `apply_accepted_ceiling` (lib/memory-budget.sh) downgrades an over-hard-line verdict
			# to a loud warning only when this deploy is at or under that number. No ceiling set:
			# today's behaviour, unchanged — refuse. See RUNBOOK.md, 'the memory budget' for the
			# decision this asks the owner to make before the first deploy that needs it.
			final_line=$(apply_accepted_ceiling "$verdict" "$percent" "$expected_mb" "${EDGE_BUDGET_ACCEPTED_MB:-}")
			final_verdict=${final_line%% *}

			case "$final_verdict" in
			ok) ok "memory budget ($priced_as): ${expected_mb}M of ${mem_total_mb}M MemTotal (${percent}%)" ;;
			warn)
				echo "  warn  memory budget ($priced_as): ${expected_mb}M of ${mem_total_mb}M MemTotal (${percent}%)" >&2
				if [ "$verdict" = "refuse" ]; then
					echo "        — OVER THE HARD BUDGET (${hard_percent}%), accepted: EDGE_BUDGET_ACCEPTED_MB=${EDGE_BUDGET_ACCEPTED_MB} covers it." >&2
					echo "        This box is overcommitted by owner decision — more RAM (a VM resize) or de-listing" >&2
					echo "        a product would bring it back under the hard line. Still deploying — see" >&2
					echo "        RUNBOOK.md, 'the memory budget'." >&2
				else
					echo "        — over the ${soft_percent}% soft line. Still deploying; see RUNBOOK.md, 'the memory budget'." >&2
				fi
				;;
			refuse)
				if [ -n "${EDGE_BUDGET_ACCEPTED_MB:-}" ]; then
					bad "memory budget ($priced_as): ${expected_mb}M of ${mem_total_mb}M MemTotal (${percent}%) — over the hard budget AND over the owner-accepted ceiling (EDGE_BUDGET_ACCEPTED_MB=${EDGE_BUDGET_ACCEPTED_MB}M). Raise the accepted ceiling (a deliberate decision, not a default) or trim COMPOSE_PROFILES/EDGE_PROFILES or consolidate a service."
				else
					bad "memory budget ($priced_as): ${expected_mb}M of ${mem_total_mb}M MemTotal (${percent}%) — over the hard budget, and no EDGE_BUDGET_ACCEPTED_MB is set in .env. Set it after reading the projection (RUNBOOK.md, 'the memory budget'), trim COMPOSE_PROFILES/EDGE_PROFILES, or consolidate a service."
				fi
				;;
			esac

			# Informational only, from here on — L8: `record_budget` (remote.sh) writes this
			# after every healthy deploy; printed for reference, never read by the verdict above.
			last_ok_file="${EDGE_STATE_DIR:-/srv/opsgrids/state}/edge-budget.last-ok"
			if [ -f "$last_ok_file" ]; then
				last_ok_mb=$(tr -d '[:space:]' <"$last_ok_file" 2>/dev/null || echo "")
				case "$last_ok_mb" in '' | *[!0-9]*) ;; *) echo "  info  last successful deploy priced at: ${last_ok_mb}M (informational only — not used in the verdict above)" ;; esac
			fi

			# Informational only — never gates a deploy, and never mixed into $expected_mb above:
			# a backlog placeholder in memory-budget.json's `planned` key must not be able to fail
			# a real deploy. This is what tells whoever is watching a deploy whether the box is
			# still projected to have room for what is coming, not just for what is running now.
			planned_mb=$(python3 lib/memory_budget_totals.py "$budget_file" --profiles "$declared_profiles" --with-planned) || planned_mb=""
			if [ -n "$planned_mb" ] && [ "$planned_mb" -gt "$expected_mb" ]; then
				projected_percent=$((planned_mb * 100 / mem_total_mb))
				echo "  info  projected with the planned backlog too: ${planned_mb}M of ${mem_total_mb}M (${projected_percent}%) — see memory-budget.json's 'planned' key"
			fi
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

# The catalogue says which products are sold; COMPOSE_PROFILES/EDGE_PROFILES says which
# product services run. Two independent questions get asked here, both only checkable once
# Postgres exists (on the first deploy it does not, and the rows come from migrations that have
# not run yet):
#
#   1. Commercial listing agreement (ADR-0013 §5, unchanged by this fix round): an `available`
#      product with no service behind it is a 504 the customer finds.
#   2. Real-customer-impact advisory (H2, the memory-headroom slice's 2026-09-23 fix round,
#      replacing the previous "zero-activation profile auto-off" — see the note above
#      `declared_profiles` for why that auto-off was removed rather than fixed in place): a
#      product with at least one real activation whose profile this box does not run is refused
#      outright — turning that profile off would take a paying customer's product away, silently,
#      the moment this deploy lands. A profile that is on, has zero activations, AND is not even
#      sold in the catalogue is reported as information only: safe to consider trimming, never
#      forced.
#
# `profile_for`/`key_for_profile` came from lib/active-profiles.sh, sourced above.
if docker compose -f compose.prod.yml --env-file .env ps --status running postgres 2>/dev/null | grep -q postgres; then
	catalogue_rc=0
	available=$(docker compose -f compose.prod.yml --env-file .env exec -T postgres \
		psql -U opsgrids -d opsgrids -Atc "select key from product.products where status = 'available' and not is_platform" 2>/dev/null) || catalogue_rc=$?
	# An empty answer and a failed question look the same to the loop below, and a green line
	# for a check that never ran is worse than no check.
	[ "$catalogue_rc" -eq 0 ] || bad "could not read the product catalogue (psql exit $catalogue_rc): the catalogue/profile agreement is unverified"

	enabled=",${declared_profiles},"
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
		bad "sold in the catalogue but no service will run for it:$sold_unserved — add the profile to COMPOSE_PROFILES/EDGE_PROFILES or set the product to 'soon'"; fi
	for profile in $(echo "$declared_profiles" | tr ',' ' '); do
		key=$(key_for_profile "$profile")
		[ -n "$key" ] || continue
		case " $available " in
		*" $key "*) ;;
		*) echo "  warn  profile '$profile' runs but the catalogue does not sell '$key' yet (status is not 'available')" >&2 ;;
		esac
	done

	# ---- the real-customer-impact advisory (H2/H3) ----
	# L12 (review round 2): only an activation that is actually live counts — `trial` or `active`,
	# not a `suspended` row nobody is being served for — and never an end-to-end test
	# organization (`e2e/conftest.py` names every one it creates `e2e-…`; a seeded test org must
	# not be the reason a real product profile looks "in use", or a real customer-impact refusal
	# fire over a fixture). Read-only, across schemas, as the box's own superuser — there is no
	# foreign key between the two, only this join.
	activations_rc=0
	activated_keys=$(docker compose -f compose.prod.yml --env-file .env exec -T postgres \
		psql -U opsgrids -d opsgrids -Atc "select distinct a.product_key from product.activations a where a.status in ('trial','active') and not exists (select 1 from account.organizations o where o.id = a.organization_id and o.slug like 'e2e-%')" 2>/dev/null) || activations_rc=$?
	if [ "$activations_rc" -ne 0 ]; then
		# H3: a query failure is not evidence of zero activations — it is evidence of nothing at
		# all, and must never be read as "safe to turn this profile off". Skipped with a warning,
		# not silently treated as an empty result.
		echo "  warn  could not read product.activations (psql exit $activations_rc): the activation advisory is skipped this deploy — this is NOT the same as zero activations" >&2
	else
		# shellcheck disable=SC2086 # word-splitting activated_keys (one product key per line) on purpose
		activated_list=$(activated_profiles $activated_keys)
		activated_set=" $(echo "$activated_list" | tr '\n' ' ') "

		unserved_customers=""
		for profile in $activated_list; do
			case "$enabled" in
			*",$profile,"*) ;;
			*) unserved_customers="$unserved_customers $profile" ;;
			esac
		done
		if [ -z "$unserved_customers" ]; then
			ok "every profile with a real activation is enabled"
		else
			bad "product(s) with active organizations have no service running — customers would lose access: profile(s)$unserved_customers. Add to COMPOSE_PROFILES/EDGE_PROFILES; do not remove a profile with a real activation."
		fi

		unused_and_unsold=""
		for profile in $(echo "$declared_profiles" | tr ',' ' '); do
			case "$activated_set" in
			*" $profile "*) continue ;; # has at least one activation — not the same signal
			esac
			key=$(key_for_profile "$profile")
			[ -n "$key" ] || continue
			case " $available " in
			*" $key "*) continue ;; # still commercially listed, just no takers yet
			esac
			unused_and_unsold="$unused_and_unsold $profile"
		done
		if [ -n "$unused_and_unsold" ]; then
			echo "  info  profile(s) enabled with zero activations and not sold in the catalogue:$unused_and_unsold — costing a container group for nothing; safe to consider trimming (never forced)." >&2
		fi
	fi
else
	echo "  warn  Postgres not running yet: the catalogue/profile agreement and activation checks are skipped on this deploy" >&2
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
