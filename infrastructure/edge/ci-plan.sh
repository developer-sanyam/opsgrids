#!/usr/bin/env bash
# Decides what a commit needs deployed, for .github/workflows/deploy.yml.
#
# A deploy costs the box CPU it does not have to spare (two cores, ~59 containers), so the
# pipeline does only what the commit calls for: the services deploy runs when something
# outside the front ends and the docs changed, and only the app bundles whose sources
# changed are rebuilt and published. A shared front-end package changing means every app.
#
# Usage: ci-plan.sh <mode> [services] [apps]
#   mode      "diff"  compare HEAD with $BASE, the ref the box last served healthily
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
ALL_APPS=(buyer dashboard operations hrms crm pulseboard projects support marketing finance itsm storefront engage)
dir_for() {
	case "$1" in
	# buyer and storefront are two entry points of one package, so the map is many-to-one and
	# a change under product-storefront must plan both. Every other app is named after its own
	# directory and falls through to the default — engage included.
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
	# What the box does not have yet — not what the last commit changed. BASE is the ref the box
	# recorded as its last good deploy. Diffing HEAD~1 instead loses changes whenever a commit
	# never deployed: CI cancels a superseded run on main, a deploy fails, several commits are
	# pushed at once. With no usable base (a new box, a rewritten history) deploy everything.
	base="${BASE:-}"
	# The box is already at this commit, or at one built on it: nothing to deploy. CI no longer
	# cancels a superseded run on main (every main commit gets its own complete run), so a run
	# for an older commit can finish after a newer one has already deployed — and without this
	# the check below reads that as "no usable base" and deploys *everything* from the older
	# commit, a rollback nobody asked for. Equal is the re-run case, which the diff handles too.
	if [ -n "$base" ] && git cat-file -e "${base}^{commit}" 2>/dev/null &&
		git merge-base --is-ancestor HEAD "$base" 2>/dev/null; then
		echo "plan: the box already serves ${base} (this commit or a newer one): nothing to deploy" >&2
		out="${GITHUB_OUTPUT:-/dev/stdout}"
		{
			echo "services=false"
			echo "apps=[]"
		} >>"$out"
		exit 0
	fi
	if [ -z "$base" ] || ! git merge-base --is-ancestor "$base" HEAD 2>/dev/null; then
		echo "plan: no usable base ref ('${base:-none}'): deploying everything" >&2
		exec "$0" all true all
	fi
	changed=$(git diff --name-only "$base" HEAD)
	# Services: anything that is not a front end, a document, a test journey or the pipeline.
	# (An empty diff is one empty line, which matches nothing and would read as "changed".)
	if [ -n "$changed" ] && echo "$changed" | grep -qvE '^(frontend/|docs/|e2e/|\.github/|\.claude/)|\.md$'; then
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
