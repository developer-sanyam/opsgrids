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
# Keys that are allowed to be empty, each for a stated reason.
optional_empty="PII_DECRYPT_KEYS SENTRY_DSN BACKUP_S3_ENDPOINT"

# shellcheck disable=SC1091  # .env is data, generated on the box from .env.example
set -a
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
	replace-me* | CHANGE_ME* | dev-only-* | ci-only-* | *example.com* | 198.51.100.7)
		# 198.51.100.7 is the RFC 5737 documentation address used in the ADR and in
		# .env.example. Left in place, the merchant UI prints an address that reaches nobody.
		# `ops@example.com` is the committed ACME placeholder; a real deploy needs an inbox a
		# human reads, because in slice 1 Let's Encrypt's expiry email is the only notification
		# that exists.
		placeholder="$placeholder $key"
		;;
	esac
done

[ -z "$missing" ] && ok "every key in .env.example is set" ||
	bad "missing or empty in .env:$missing"
[ -z "$placeholder" ] && ok "no placeholder values" ||
	bad "still carrying a placeholder value:$placeholder"

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
[ "${inodes:-0}" -lt 90 ] && ok "inodes ${inodes}% used" || bad "inodes ${inodes}% used"

docker info >/dev/null 2>&1 && ok "docker daemon responding" || bad "docker daemon not responding"

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
