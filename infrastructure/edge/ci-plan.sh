#!/usr/bin/env bash
# Decides what a commit needs deployed, for .github/workflows/deploy.yml.
#
# A deploy costs the box CPU it does not have to spare (two cores, ~59 containers), so the
# pipeline does only what the commit calls for: the services deploy runs when something
# outside the front ends and the docs changed, and only the app bundles whose sources
# changed are rebuilt and published. A shared front-end package changing means every app.
#
# Usage: ci-plan.sh <mode> [services] [apps]
#   mode      "diff"  compare HEAD with HEAD~1 (a squash-merged PR is exactly one commit)
#             "all"   deploy everything (manual runs and deploy/* branches)
#   services  manual override: "true" | "false"            (mode=all only)
#   apps      manual override: "all" | "none" | "a,b,c"    (mode=all only)
#
# Writes `services=<true|false>` and `apps=<json array>` to $GITHUB_OUTPUT (or stdout).
set -euo pipefail

mode="${1:?mode: diff|all}"
want_services="${2:-true}"
want_apps="${3:-all}"

# app name (publish-web.sh APP=) -> the workspace package whose sources it is built from
ALL_APPS=(buyer dashboard operations hrms crm pulseboard projects support marketing finance itsm storefront)
dir_for() {
	case "$1" in
	buyer | storefront) echo product-storefront ;;
	dashboard) echo opsgrids-dashboard ;;
	*) echo "product-$1" ;;
	esac
}

json() { # words -> ["a","b"]
	local out="" w
	for w in "$@"; do out="$out${out:+,}\"$w\""; done
	echo "[$out]"
}

services=false
apps=()

if [ "$mode" = "all" ]; then
	services="$want_services"
	case "$want_apps" in
	all) apps=("${ALL_APPS[@]}") ;;
	none | "") apps=() ;;
	*)
		IFS=',' read -r -a requested <<<"$want_apps"
		for a in "${requested[@]}"; do
			a="${a// /}"
			case " ${ALL_APPS[*]} " in
			*" $a "*) apps+=("$a") ;;
			*) echo "unknown app '$a' — one of: ${ALL_APPS[*]}" >&2 && exit 64 ;;
			esac
		done
		;;
	esac
else
	changed=$(git diff --name-only HEAD~1 HEAD)
	# Services: anything that is not a front end, a document, a test journey or the pipeline.
	if echo "$changed" | grep -qvE '^(frontend/|docs/|e2e/|\.github/|\.claude/|[^/]*\.md$)'; then
		services=true
	fi
	# Apps: a shared package or the lockfile rebuilds them all; otherwise each app's own dir.
	if echo "$changed" | grep -qE '^frontend/(opsgrids-ui|opsgrids-client|opsgrids-config|pnpm-lock\.yaml|pnpm-workspace\.yaml)'; then
		apps=("${ALL_APPS[@]}")
	else
		for a in "${ALL_APPS[@]}"; do
			if echo "$changed" | grep -q "^frontend/$(dir_for "$a")/"; then apps+=("$a"); fi
		done
	fi
fi

out="${GITHUB_OUTPUT:-/dev/stdout}"
{
	echo "services=$services"
	echo "apps=$(json "${apps[@]+"${apps[@]}"}")"
} >>"$out"
echo "plan: services=$services apps=${apps[*]:-none}" >&2
