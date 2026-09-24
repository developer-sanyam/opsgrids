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
# Empty means "derive from COMPOSE_PROFILES" (`OpsMonitorSettings`'s own `expected_api_containers`/
# `expected_dlq_queues`) — a perfectly valid state for every box, not a placeholder to fill in;
# set explicitly only to narrow or override that computed list. SMTP_USE_SSL false is also the
# ordinary default (STARTTLS on 587, not implicit TLS on 465) for every box that has not been
# told to use a provider that only offers SMTPS.
optional_empty="$optional_empty OPS_MONITOR_API_CONTAINERS OPS_MONITOR_DLQ_QUEUES SMTP_USE_SSL"
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
# Empty is the honest state for a deployment not yet registered for tax: the billing run then
# writes no tax line (ADR-0005 addendum, 2026-09-23).
optional_empty="$optional_empty BILLING_TAX_CODE BILLING_TAX_JURISDICTION"
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

# `.env` is sourced as SHELL below, and this file exists to fail with a message, not with
# whatever bash makes of a stray character. Checked on the RAW file, before it is sourced, and
# nothing is sourced if a line is found:
#
#   * An unquoted value containing whitespace — `EDGE_PROFILES=inventory, storefront`,
#     `OPS_ALERT_EMAIL=a and b` — is not one assignment: bash assigns the text before the first
#     space and then RUNS the rest as a command (exit 127 "command not found" at best, with no
#     FAILED footer; at worst something that exists). docker compose parses the same line as a
#     single value, so the two would also disagree about what the box says.
#   * An unquoted value containing a shell metacharacter — `; & | < > ( )`, a backtick, `$(` —
#     is run, backgrounded or redirected the same way.
#   * Text after a closing quote — `KEY="a" b` — is a second word.
#   * CRLF line endings: bash keeps the carriage return on the end of every value it sources
#     (`EDGE_HOST` would be `edge.opsgrids.com\r`), where docker compose strips it.
#
# Quoted values (`KEY="a b"`), a trailing space, an inline ` # comment` after a value, and
# `export KEY=…` (checked like any assignment) are fine and are not flagged. A quoted value with a
# backslash in it is left alone rather than second-guessed.
env_problems=""
env_crlf=""
while IFS= read -r env_line || [ -n "$env_line" ]; do
	case "$env_line" in
	*$'\r')
		env_crlf=1
		env_line=${env_line%$'\r'}
		;;
	esac
	env_line=${env_line#export }
	case "$env_line" in
	[A-Za-z_]*=*) ;;
	*) continue ;;
	esac
	env_key=${env_line%%=*}
	case "$env_key" in
	*[!A-Za-z0-9_]*) continue ;;
	esac
	env_value=${env_line#*=}
	case "$env_value" in
	\"*)
		# quoted: the shell reads it as one word, and so does compose — unless something follows
		# the closing quote.
		case "$env_value" in *\\*) continue ;; esac
		env_rest=${env_value#\"}
		case "$env_rest" in *\"*) ;; *) continue ;; esac
		env_after=${env_rest#*\"}
		;;
	\'*)
		case "$env_value" in *\\*) continue ;; esac
		env_rest=${env_value#\'}
		case "$env_rest" in *\'*) ;; *) continue ;; esac
		env_after=${env_rest#*\'}
		;;
	*)
		env_after=""
		env_value=$(printf '%s' "$env_value" | sed -e 's/[[:space:]]\{1,\}#.*$//' -e 's/[[:space:]]\{1,\}$//')
		case "$env_value" in
		*[[:space:]]*)
			env_problems="${env_problems}${env_key} in .env has an unquoted value containing whitespace — bash would run the words after the first space as a command. Quote the whole value: ${env_key}=\"…\""$'\n'
			;;
		*)
			if printf '%s' "$env_value" | grep -Eq '[;&|<>()`]|\$\('; then
				env_problems="${env_problems}${env_key} in .env has an unquoted value containing a shell metacharacter (; & | < > ( ) \` or \$( ) — bash would run, background or redirect it. Quote the whole value: ${env_key}=\"…\""$'\n'
			fi
			;;
		esac
		;;
	esac
	env_after=$(printf '%s' "$env_after" | sed -e 's/^[[:space:]]\{1,\}#.*$//' -e 's/^[[:space:]]\{1,\}$//')
	if [ -n "$env_after" ]; then
		env_problems="${env_problems}${env_key} in .env has text after its closing quote — bash reads it as a separate word (a command or an argument). Put the whole value inside the quotes: ${env_key}=\"…\""$'\n'
	fi
done <.env
if [ -n "$env_crlf" ]; then
	env_problems="${env_problems}.env has Windows (CRLF) line endings — bash keeps the carriage return on the end of every value it sources, where docker compose strips it, so the two would disagree. Convert it: sed -i 's/\\r\$//' .env"$'\n'
fi
if [ -n "$env_problems" ]; then
	while IFS= read -r env_problem; do
		[ -n "$env_problem" ] && bad "$env_problem"
	done <<<"$env_problems"
	echo "preflight: FAILED — nothing was changed (.env was not read: fix the line(s) above first)." >&2
	exit 1
fi

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
# `COMPOSE_PROFILES=*` ("every profile") is valid for docker compose and for ops-monitor, but the
# advisories below match profile names one at a time, and a literal `*` matches none of them —
# every sold product would read as unserved. Expanded here to the compose file's own profile
# list; `bad` (not a silent fallback) if compose cannot list it.
case ",${declared_profiles}," in
*",*,"*)
	if all_compose_profiles=$(docker compose -f compose.prod.yml --env-file .env config --profiles | tr '\n' ',' | sed 's/,$//') && [ -n "$all_compose_profiles" ]; then
		declared_profiles=$(expand_profile_wildcard "$declared_profiles" "$all_compose_profiles")
		ok "COMPOSE_PROFILES=* expanded to: $declared_profiles"
	else
		bad "COMPOSE_PROFILES contains '*' but 'docker compose config --profiles' could not list this file's profiles to expand it (compose's own error is above)"
		declared_profiles=""
	fi
	;;
esac
if [ -n "${EDGE_PROFILES:-}" ]; then
	ok "COMPOSE_PROFILES overridden by EDGE_PROFILES: $declared_profiles"
fi
# A profile name compose does not define enables nothing — silently: `COMPOSE_PROFILES=hrm` is
# priced, judged and started as core only, and the products the box sells simply are not there.
# Checked against the compose file's own list. (If compose cannot list its profiles the memory
# row below fails closed on the same error, so nothing is skipped quietly here.)
if [ -n "$declared_profiles" ] && known_profiles=$(docker compose -f compose.prod.yml --env-file .env config --profiles 2>/dev/null); then
	for declared_profile in $(echo "$declared_profiles" | tr ',' ' '); do
		case $'\n'"$known_profiles"$'\n' in
		*$'\n'"$declared_profile"$'\n'*) ;;
		*) bad "profile '$declared_profile' (from EDGE_PROFILES/COMPOSE_PROFILES) is not defined in compose.prod.yml, so it enables nothing. Defined: $(echo "$known_profiles" | tr '\n' ' ')" ;;
		esac
	done
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

# The same strength rules the services enforce at startup (see the library's header), checked
# here so a weak or reused secret stops the deploy before anything changes.
# shellcheck disable=SC1091  # sourced, not executed — see the file's own header
. ./lib/secret-strength.sh
secret_problems=$(secret_strength_problems)
if [ -z "$secret_problems" ]; then ok "secrets are long enough and distinct"; else
	while IFS= read -r problem; do bad "$problem"; done <<<"$secret_problems"
fi

# notification-service's own two boot-time refusals (NOTIF-A), checked here for the same reason:
# SMTP_USE_TLS and SMTP_USE_SSL both true, and EMAIL_TRANSPORT=smtp with a credential set and
# neither flag true — either stops the notification worker from starting at all.
# shellcheck disable=SC1091  # sourced, not executed — see the file's own header
. ./lib/smtp-tls.sh
smtp_tls_problems_found=$(smtp_tls_problems)
if [ -z "$smtp_tls_problems_found" ]; then ok "SMTP_USE_TLS/SMTP_USE_SSL are a combination notification-service will start with"; else
	while IFS= read -r problem; do bad "$problem"; done <<<"$smtp_tls_problems_found"
fi

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
	# The gate fails closed everywhere else (round 3): a budget it cannot read is not a budget it
	# can skip. (`remote.sh record-budget` still skips — recording a number is not a verdict.)
	bad "no $budget_file — the memory budget cannot be judged"
elif ! command -v python3 >/dev/null 2>&1; then
	bad "no python3 on this box — the memory budget cannot be priced or judged"
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
		# Fails closed (round 3, HIGH): a compose/docker failure used to yield an empty post-deploy
		# set and a green "0M (0%)" line. Now it is a FAILED row that says why, and every memory
		# line below is skipped — there is no total to judge.
		bad "memory budget could not be priced — ${POST_DEPLOY_ERROR:-no reason given}. The gate refuses to pass on a total it could not compute."
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
	*[!0-9]* | 0?*) bad "EDGE_BUDGET_ACCEPTED_MB (${EDGE_BUDGET_ACCEPTED_MB}) must be a whole number of megabytes (no sign, unit or leading zero)" ;;
	esac

	# Read from the budget file itself rather than hardcoded here — this file's own
	# `host.soft_budget_percent`/`hard_budget_percent`/`max_accepted_percent` is what `tests/infra/
	# test_compose_memory_budget.py` pins, and a hardcoded copy here would silently drift from
	# it the next time any of them changes.
	soft_percent=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['host']['soft_budget_percent'])" "$budget_file")
	hard_percent=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['host']['hard_budget_percent'])" "$budget_file")
	max_accepted_percent=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['host']['max_accepted_percent'])" "$budget_file")
	if [ -n "$expected_mb" ]; then
		mem_total_kb=$(awk '/^MemTotal:/ {print $2}' "${EDGE_MEMINFO_FILE:-/proc/meminfo}" 2>/dev/null || echo "")
		if ! [[ "$mem_total_kb" =~ ^[1-9][0-9]*$ ]]; then
			# Same principle as post_deploy_compute: no reading is not "fine". (This used to warn
			# and skip — the one branch of the gate that still failed open.)
			bad "could not read MemTotal from ${EDGE_MEMINFO_FILE:-/proc/meminfo} — the memory budget cannot be judged"
		else
			verdict_line=$(memory_budget_verdict "$mem_total_kb" "$expected_mb" "$soft_percent" "$hard_percent")
			verdict=${verdict_line%% *}
			percent=${verdict_line#* }
			mem_total_mb=$((mem_total_kb / 1024))
			cap_mb=$((mem_total_mb * max_accepted_percent / 100))
			hard_mb=$((mem_total_mb * hard_percent / 100))
			usage=$(describe_budget_usage "$expected_mb" "$mem_total_mb")

			# B2/H3/H4 (review round 2): an explicit owner ceiling replaces the previous
			# grandfather-vs-last-ok ratchet. `EDGE_BUDGET_ACCEPTED_MB` in `.env`, if set, is the
			# total (MB) the owner has actually looked at the projection and agreed to run at —
			# `apply_accepted_ceiling` (lib/memory-budget.sh) downgrades an over-hard-line verdict
			# to a loud warning only when this deploy is at or under that number. No ceiling set:
			# today's behaviour, unchanged — refuse. See RUNBOOK.md, 'the memory budget' for the
			# decision this asks the owner to make before the first deploy that needs it.
			#
			# Round 3 (M5): the ceiling has an upper bound, `host.max_accepted_percent` of MemTotal.
			# A ceiling above it is a FAILED row (a `38940` for `3894` typo would otherwise be a
			# blank cheque) and is ignored for the verdict; and no ceiling — however correct — can
			# cover a total above it.
			accepted="${EDGE_BUDGET_ACCEPTED_MB:-}"
			accepted_effective=""
			case "$accepted" in
			'' | *[!0-9]* | 0?*) ;; # unset, or already reported above
			*)
				# `${#accepted} -gt 7`: a twenty-digit value overflows bash's integer arithmetic and
				# compares as a negative number — seven digits is already ten terabytes.
				if [ "${#accepted}" -gt 7 ] || [ "$accepted" -gt "$cap_mb" ]; then
					bad "EDGE_BUDGET_ACCEPTED_MB (${accepted}M) is above the ${max_accepted_percent}% cap (${cap_mb}M of ${mem_total_mb}M MemTotal) — a typo? The ceiling is the exact post-deploy total preflight printed for this commit (RUNBOOK.md, 'the memory budget'), and it never legitimately exceeds the cap."
				else
					accepted_effective="$accepted"
				fi
				;;
			esac
			final_line=$(apply_accepted_ceiling "$verdict" "$percent" "$expected_mb" "$accepted_effective" "$cap_mb")
			final_verdict=${final_line%% *}

			case "$final_verdict" in
			ok) ok "memory budget ($priced_as): $usage" ;;
			warn)
				echo "  warn  memory budget ($priced_as): $usage" >&2
				if [ "$verdict" = "refuse" ]; then
					echo "        — OVER THE HARD BUDGET (${hard_percent}% = ${hard_mb}M), accepted: EDGE_BUDGET_ACCEPTED_MB=${accepted} covers it." >&2
					echo "        This box is overcommitted by owner decision — more RAM (a VM resize) or de-listing" >&2
					echo "        a product would bring it back under the hard line. Still deploying — see" >&2
					echo "        RUNBOOK.md, 'the memory budget'." >&2
				else
					echo "        — over the ${soft_percent}% soft line. Still deploying; see RUNBOOK.md, 'the memory budget'." >&2
				fi
				;;
			refuse)
				if [ "$expected_mb" -gt "$cap_mb" ]; then
					bad "memory budget ($priced_as): $usage — over the absolute cap (${max_accepted_percent}% of MemTotal = ${cap_mb}M), which no EDGE_BUDGET_ACCEPTED_MB can cover. De-list a product (COMPOSE_PROFILES/EDGE_PROFILES), consolidate a service, or resize the VM."
				elif [ -n "$accepted_effective" ]; then
					bad "memory budget ($priced_as): $usage — over the hard budget AND over the owner-accepted ceiling (EDGE_BUDGET_ACCEPTED_MB=${accepted}M, $((expected_mb - accepted))M short). Raise the accepted ceiling (a deliberate decision, not a default) or trim COMPOSE_PROFILES/EDGE_PROFILES or consolidate a service."
				elif [ -n "$accepted" ]; then
					bad "memory budget ($priced_as): $usage — over the hard budget, and the EDGE_BUDGET_ACCEPTED_MB in .env was rejected (see above), so nothing accepts it."
				else
					bad "memory budget ($priced_as): $usage — over the hard budget, and no EDGE_BUDGET_ACCEPTED_MB is set in .env. Set it after reading the projection (RUNBOOK.md, 'the memory budget'), trim COMPOSE_PROFILES/EDGE_PROFILES, or consolidate a service."
				fi
				;;
			esac

			# The ceiling is a decision record — exactly the printed total for the commit being
			# deployed (RUNBOOK.md) — so any slack, or a ceiling this deploy does not need, is worth
			# saying: it is an overcommit the owner agreed to that this deploy is not asking for.
			if [ -n "$accepted_effective" ]; then
				if [ "$verdict" = "refuse" ] && [ "$expected_mb" -le "$accepted_effective" ] && [ "$accepted_effective" -gt "$expected_mb" ]; then
					echo "  info  EDGE_BUDGET_ACCEPTED_MB (${accepted_effective}M) is $((accepted_effective - expected_mb))M above this deploy's total (${expected_mb}M) — slack the owner accepted but this commit does not use; set it to ${expected_mb} so the file records what was actually decided."
				elif [ "$verdict" != "refuse" ]; then
					echo "  info  EDGE_BUDGET_ACCEPTED_MB (${accepted_effective}M) is set but not needed: this deploy is under the hard line (${expected_mb}M against ${hard_mb}M) — clear it, so a future deploy that does go over is not silently pre-approved."
				fi
			fi

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
			# Projected on top of *this deploy's own* total, not a fresh profile-list one.
			core_mb=$(python3 lib/memory_budget_totals.py "$budget_file" --profiles "") || core_mb=""
			core_planned_mb=$(python3 lib/memory_budget_totals.py "$budget_file" --profiles "" --with-planned) || core_planned_mb=""
			if [ -n "$core_mb" ] && [ -n "$core_planned_mb" ] && [ "$core_planned_mb" -gt "$core_mb" ]; then
				projected_mb=$((expected_mb + core_planned_mb - core_mb))
				echo "  info  projected with the planned backlog too: $(describe_budget_usage "$projected_mb" "$mem_total_mb") — see memory-budget.json's 'planned' key"
			fi
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
# The billing run is off unless .env says so (ADR-0005 addendum, review fix 6): on a box with
# history its first passes convert every expired trial and bill every overdue period. Unset is
# legal — and loud, because a box left like this converts no trial and invoices nothing.
if [ "${BILLING_RUN_ENABLED:-}" != "true" ]; then
	echo "  warn  BILLING_RUN_ENABLED is ${BILLING_RUN_ENABLED:-unset}: the billing run is OFF. No trial converts and nothing is invoiced. Read the runbook's \"The billing run\" first." >&2
else
	# On, it needs its cutover (final review, fix 3). Without one — or with one the worker refuses
	# (the same shape app/config.py accepts: YYYY-MM-DDTHH:MM[:SS[.f]] then Z or ±HH:MM) — the run
	# does not start. Checked here too so a bad value never reaches a deploy: a regex alone passes
	# 2026-09-31, so GNU date must also read it as a real instant.
	if [ -z "${BILLING_RUN_NOT_BEFORE:-}" ]; then
		bad "BILLING_RUN_ENABLED=true but BILLING_RUN_NOT_BEFORE is empty — set it to the moment billing goes live, e.g. 2026-10-01T09:00:00+05:30 (the runbook's \"The billing run\")"
	elif ! printf '%s' "$BILLING_RUN_NOT_BEFORE" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:[0-9]{2})$'; then
		bad "BILLING_RUN_NOT_BEFORE must be YYYY-MM-DDTHH:MM:SS±HH:MM, e.g. 2026-10-01T09:00:00+05:30 or 2026-10-01T03:30:00Z"
	elif ! cutover_epoch=$(date -u -d "$BILLING_RUN_NOT_BEFORE" +%s 2>/dev/null); then
		bad "BILLING_RUN_NOT_BEFORE ($BILLING_RUN_NOT_BEFORE) is not a real instant"
	else
		if [ "$cutover_epoch" -gt "$(date -u +%s)" ]; then
			echo "  warn  BILLING_RUN_NOT_BEFORE ($BILLING_RUN_NOT_BEFORE) is in the future: the run rolls periods but nothing is invoiced until then." >&2
		fi
		ok "billing run enabled, cutover $BILLING_RUN_NOT_BEFORE"
	fi
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

# The operations reconcile sweep, storefront's custom-domain sweep and document's purge sweep run
# inside their own API containers (`RUN_SWEEPS_IN_API`, compose.prod.yml defaults it to `true`;
# ADR-0013 fix round 2). Anything else in `.env` turns them OFF, and — since the `*-cron`
# containers that used to carry them no longer exist — nothing runs them at all: no stock
# reconciliation, no domain verification, no purge of soft-deleted uploads past their retention
# window, with every container healthy. Loud, not fatal: an operator may be stopping a sweep on
# purpose for a maintenance window, but never by accident.
# Read the way the services read it (pydantic's bool: true/True/1/yes/on/y/t, any case) — a
# `True` in `.env` turns the sweeps ON and must not be warned about as off.
sweeps_flag=$(printf '%s' "${RUN_SWEEPS_IN_API:-true}" | tr '[:upper:]' '[:lower:]')
case "$sweeps_flag" in
true | 1 | yes | on | y | t) ok "in-API sweeps enabled (operations reconcile, storefront domains, document purge)" ;;
*)
	echo "  warn  RUN_SWEEPS_IN_API=${RUN_SWEEPS_IN_API}: the operations, storefront and document sweeps are OFF and" >&2
	echo "        no separate cron container exists to run them — nothing reconciles stock, verifies" >&2
	echo "        custom domains or purges deleted uploads until it is 'true' again." >&2
	;;
esac

# ops-monitor's own state subdirectory (C10, M2): it runs as the `product` image's baked-in
# non-root user, not `deploy`, so it can only write there if `remote.sh up`'s
# `ensure_ops_monitor_state_dir` has already chowned it to that uid. A mismatch here means the
# container will crash on its very first state/heartbeat write — not fatal to preflight (the
# next deploy's `up` step fixes it automatically), but worth a human noticing sooner than that.
ops_monitor_state_dir="${EDGE_STATE_DIR:-/srv/opsgrids/state}/ops-monitor"
if [ -d "$ops_monitor_state_dir" ]; then
	owner_uid=$(stat -c '%u' "$ops_monitor_state_dir" 2>/dev/null || stat -f '%u' "$ops_monitor_state_dir")
	image_uid=$(docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T --entrypoint id product -u 2>/dev/null || echo "")
	if [ -z "$image_uid" ]; then
		echo "  warn  could not determine the product image's uid (is it built yet?) — skipping the ops-monitor state dir ownership check" >&2
	elif [ "$owner_uid" != "$image_uid" ]; then
		echo "  warn  $ops_monitor_state_dir is owned by uid $owner_uid; the product image runs as uid $image_uid." >&2
		echo "        ops-monitor will crash on its first write until this matches — 'make deploy' fixes it" >&2
		echo "        automatically (remote.sh's ensure_ops_monitor_state_dir), or run it by hand:" >&2
		echo "        ./remote.sh up" >&2
	else
		ok "ops-monitor state dir owned by the image's own uid ($owner_uid)"
	fi
else
	echo "  warn  $ops_monitor_state_dir does not exist yet — created and chowned automatically on the next 'make deploy'" >&2
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
done < <(sed -n 's#^https://\([a-z0-9.-]\{1,\}\) {$#\1#p' caddy/Caddyfile)

# A prefix in the route table whose upstream is not among this box's services is a hung
# connection and a 504 for whoever calls it. Product routes are covered by the catalogue check
# below; this catches the ones with no product key (approvals runs only with the hrms profile).
running_services=" $(docker compose -f compose.prod.yml --env-file .env config --services 2>/dev/null | tr '\n' ' ' || true) "
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
	# psql prints one key per line; the `case` matches below need them space-separated. Matching
	# `" $available "` against newline-separated output only ever found the FIRST key, so with two
	# or more sold products the advisory told the owner sold products were "not sold — safe to trim".
	available_set=" $(printf '%s\n' "$available" | tr '\n' ' ') "

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
		case "$available_set" in
		*" $key "*) ;;
		*) echo "  warn  profile '$profile' runs but the catalogue does not sell '$key' yet (status is not 'available')" >&2 ;;
		esac
	done

	# ---- the real-customer-impact advisory (H2/H3) ----
	# L12 (review round 2, corrected in round 3): an activation counts when the customer is, or
	# is about to be again, served by it — `trial`, `active` **and `suspended`**. The round-2
	# version dropped `suspended` on the reasoning that nobody is served for one; the re-review
	# pointed out what that misses: `product.project_subscription` flips a suspended activation
	# back to `active` the moment a payment lands, onto a service this box would by then not be
	# running — a paying customer returning to a 504. (`product.activations.status` is only ever
	# `trial | active | suspended`, so this list is every status a row can have; it is spelled out
	# so a future terminal status has to be added deliberately.) Never an end-to-end test
	# organization (`e2e/conftest.py` names every one it creates `e2e-…`; a seeded test org must
	# not be the reason a real product profile looks "in use", or a real customer-impact refusal
	# fire over a fixture). Read-only, across schemas, as the box's own superuser — there is no
	# foreign key between the two, only this join.
	activations_rc=0
	activated_keys=$(docker compose -f compose.prod.yml --env-file .env exec -T postgres \
		psql -U opsgrids -d opsgrids -Atc "select distinct a.product_key from product.activations a where a.status in ('trial','active','suspended') and not exists (select 1 from account.organizations o where o.id = a.organization_id and o.slug like 'e2e-%')" 2>/dev/null) || activations_rc=$?
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
			case "$available_set" in
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
