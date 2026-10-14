#!/usr/bin/env bash
# Publish a static bundle to the edge — build here, ship there, swap a symlink.
#
# Why bundles are not built on the box like everything else:
#
#   * The root .dockerignore excludes `frontend` and every `dist` from every build context, so
#     no service image can contain it. Un-excluding it would put the whole frontend into the
#     context of all thirty-five service builds.
#   * A static bundle is the one artefact where a failed build genuinely can leave a merchant's
#     shop broken, because there is no "old container still running" to fall back on. Building
#     on a laptop and shipping only a successful build means the box only ever receives bytes
#     that compiled.
#   * The swap is a symlink move, so publishing is atomic and rolling back is another symlink
#     move — no container restart, no downtime, no rebuild.
#
# Which bundle is `APP` (ADR-0013 §4), one line each in the table below:
#
#   buyer       the anonymous shop, served on every store host        (default; ADR-0012 §2)
#   dashboard   app.opsgrids.com — sign up, sign in, the marketplace  (ADR-0013 §4)
#   operations  ops.opsgrids.com — the Inventory product's own app    (ADR-0013 slice 3)
#
# `operations` alone also bakes in `VITE_GOOGLE_MAPS_API_KEY` from the box's `.env` (the warehouse
# map). An absent key publishes the bundle map-less behind a loud warning; `worker` and every
# other app are built without the variable. See the build section below.
#
# The buyer bundle lives at `$EDGE_WEB_ROOT/{releases,current}` — the root itself, because the
# first deploy put it there and moving a live store's directory buys nothing. Every other app
# lives at `$EDGE_WEB_ROOT/<app>/{releases,current}`, and the Caddyfile's per-host blocks point
# at exactly those paths.
#
# Usage:
#   [APP=buyer|dashboard] publish-web.sh publish  <host> <confirm-edge-host>   build, ship, swap
#   [APP=…]               publish-web.sh rollback <host> <confirm-edge-host>   swap back
#   [APP=…]               publish-web.sh releases <host> <confirm-edge-host>   what is on the box
#
# PLACEHOLDER=1 (buyer only) publishes the committed "this address is not serving a store" page
# instead of a bundle, so a box can be stood up and reached over HTTPS before the buyer entry
# point exists; it is deliberately unmistakable for a merchant's store.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"

if [ $# -lt 3 ]; then
	echo "usage: [APP=buyer|dashboard] publish-web.sh <publish|rollback|releases> <host> <confirm-edge-host>" >&2
	echo "  confirm must equal the EDGE_HOST recorded in the box .env" >&2
	exit 64
fi
action="$1"
host="$2"
confirm="$3"
repo="${EDGE_REPO:-/srv/opsgrids/repo}"
app="${APP:-buyer}"

# ---------------------------------------------------------------- the app table
# app_dir      the workspace package that builds it
# build_script its package.json script
# dist_dir     where that script writes, relative to app_dir
# entry        a file whose presence proves the entry point exists (refused otherwise)
# `app_host` is the Caddy hostname each app is actually published under (unset for `buyer`,
# which is every store's own hostname; for `dashboard`; and for `internal`, the staff console —
# all three are excluded from the AUTH_REFRESH_CORS_ORIGINS check below on purpose, see there).
# It is what that check compares against the box's own `.env`, so it has to be exactly the host
# `platformHosts.ts` and the Caddyfile name, not the logical `$app` value this script's own case
# statement keys on.
case "$app" in
buyer)
	app_dir="$repo_root/frontend/product-storefront"
	build_script="build:buyer"
	dist_dir="dist-buyer"
	entry="buyer.html"
	;;
dashboard)
	app_dir="$repo_root/frontend/opsgrids-dashboard"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	;;
operations)
	app_dir="$repo_root/frontend/product-operations"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="ops.opsgrids.com"
	;;
worker)
	# The picker/packer PWA (Fulfilment plan F3, ADR-0024 §7) — its own host, not a route under
	# `operations`, the same "one hostname per product surface" ADR-0001 gives every other app.
	# Same auth shape as every other product SPA (`RedirectToCentralLogin`,
	# `configureSession("opsgrids.worker.access_token")`), so it needs the same
	# AUTH_REFRESH_CORS_ORIGINS entry below that every other app_host does.
	app_dir="$repo_root/frontend/worker-app"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="worker.opsgrids.com"
	;;
hrms)
	app_dir="$repo_root/frontend/product-hrms"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="people.opsgrids.com"
	;;
crm)
	app_dir="$repo_root/frontend/product-crm"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="revenue.opsgrids.com"
	;;
pulseboard)
	app_dir="$repo_root/frontend/product-pulseboard"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="pulse.opsgrids.com"
	;;
projects)
	app_dir="$repo_root/frontend/product-projects"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="projects.opsgrids.com"
	;;
support)
	app_dir="$repo_root/frontend/product-support"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="support.opsgrids.com"
	;;
marketing)
	app_dir="$repo_root/frontend/product-marketing"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="marketing.opsgrids.com"
	;;
finance)
	app_dir="$repo_root/frontend/product-finance"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="finance.opsgrids.com"
	;;
itsm)
	app_dir="$repo_root/frontend/product-itsm"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="it.opsgrids.com"
	;;
storefront)
	app_dir="$repo_root/frontend/product-storefront"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="store.opsgrids.com"
	;;
engage)
	app_dir="$repo_root/frontend/product-engage"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	app_host="engage.opsgrids.com"
	;;
internal)
	# The operator/staff console: admin.opsgrids.com. Its own audience and its own gateway
	# (admin-gateway, never app-gateway — ADR-0003 §2); everything else about publishing it is
	# the same shape as every other named product app.
	app_dir="$repo_root/frontend/opsgrids-internal"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	;;
*)
	echo "unknown APP '$app' — one of: buyer, dashboard, operations, worker, hrms, crm, pulseboard, projects, support, marketing, finance, itsm, storefront, engage, internal" >&2
	exit 64
	;;
esac

ssh_box() { ssh -o BatchMode=yes "$host" "$@"; }

# Mirrors `remote.sh`'s own `dotenv_get` parsing exactly (MINOR 4 of the 2026-09-24 SSO
# review) — quoted value is what is between the quotes, an unquoted value's trailing
# ` # comment` is dropped, a CR and spaces are stripped — so a value read here for
# publishing is judged exactly the way the box's own deploy scripts read the same key, not a
# second, slightly different reading that could disagree with them. `tail -n 1` on the raw
# `sed` capture at each call site is the other half: the LAST assignment in the file wins,
# matching `dotenv_get`, not the first.
_dotenv_value() {
	local raw="$1"
	raw=${raw%$'\r'}
	case "$raw" in
	\"*)
		raw=${raw#\"}
		raw=${raw%%\"*}
		;;
	\'*)
		raw=${raw#\'}
		raw=${raw%%\'*}
		;;
	*)
		raw=$(printf '%s' "$raw" | sed -e 's/^[[:space:]]\{1,\}#.*$//' -e 's/[[:space:]]\{1,\}#.*$//')
		;;
	esac
	printf '%s' "$raw" | tr -d ' \r'
}

# The same wrong-box interlock the deploy uses. Changing what people see is a deploy.
ssh_box "$repo/infrastructure/edge/remote.sh" assert-host "$confirm"

web_root=$(ssh_box "sed -n 's/^EDGE_WEB_ROOT=//p' '$repo/infrastructure/edge/.env' | tail -n 1")
web_root=$(_dotenv_value "$web_root")
web_root="${web_root:-/srv/opsgrids/web}"
if [ "$app" = "buyer" ]; then
	app_root="$web_root"
else
	app_root="$web_root/$app"
fi
echo "app: $app   root on box: $app_root"

case "$action" in
releases)
	ssh_box "ls -1 '$app_root/releases' 2>/dev/null | sort; echo; echo -n 'current -> '; readlink -f '$app_root/current' 2>/dev/null || echo '(none)'"
	exit 0
	;;

rollback)
	# Pick the newest release that is not the one currently linked. Timestamped names sort
	# lexicographically because they are UTC and zero-padded.
	ssh_box "set -euo pipefail
      cd '$app_root/releases'
      current=\$(basename \"\$(readlink -f '$app_root/current')\")
      previous=\$(ls -1 | sort | grep -v \"^\$current\$\" | tail -1)
      if [ -z \"\$previous\" ]; then echo 'no previous release to roll back to' >&2; exit 1; fi
      ln -sfn \"releases/\$previous\" '$app_root/current.tmp'   # relative: see publish below
      mv -T '$app_root/current.tmp' '$app_root/current'
      echo \"rolled back: \$current -> \$previous\""
	echo "Caddy serves through the symlink; no restart was needed and none should be done."
	exit 0
	;;

publish) ;;
*)
	echo "unknown action: $action" >&2
	exit 64
	;;
esac

# ---------------------------------------------------------------- build
stamp=$(date -u +%Y%m%dT%H%M%SZ)
release="$app_root/releases/$stamp"

if [ "${PLACEHOLDER:-0}" = "1" ]; then
	if [ "$app" != "buyer" ]; then
		echo "refusing: PLACEHOLDER=1 only makes sense for the buyer bundle" >&2
		exit 64
	fi
	source_dir="$here/web-placeholder"
	echo "publishing the PLACEHOLDER page from $source_dir"
else
	if [ ! -f "$app_dir/$entry" ]; then
		cat >&2 <<MISSING
refusing to publish: $app_dir/$entry does not exist.

For the buyer bundle: ADR-0012 §2 puts the buyer surface in a SECOND Vite entry point —
buyer.html → main.buyer.tsx → dist-buyer/ — containing no auth code at all. Publishing the
merchant admin build in its place would ship the auth client to every anonymous shopper.
Publish the placeholder instead while you wait:  make edge-web HOST=... CONFIRM=... PLACEHOLDER=1
MISSING
		exit 1
	fi
	if ! grep -q "\"$build_script\"" "$app_dir/package.json"; then
		echo "refusing to publish: no '$build_script' script in $app_dir/package.json." >&2
		exit 1
	fi

	node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
	if [ "$node_major" -lt 22 ]; then
		echo "refusing to publish: node $node_major is on PATH; this workspace needs 22 or newer." >&2
		echo "  Vite 7 refuses older runtimes. Switch with nvm before publishing." >&2
		exit 1
	fi

	# The scheme, host and port only — an origin, never a base URL with a path. `APP_BASE_URL`
	# is documented as one (`.env.example`: "used in emails and links"), so a value with a path
	# is legitimate there and simply not what `VITE_AUTH_ORIGIN` needs: only the origin.
	_origin_of() {
		printf '%s' "$1" | sed -E 's#^([a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]+).*#\1#'
	}

	# `buyer` and `storefront` are the two entry points that ever render an uploaded image
	# (ADR-0009 §6, `theme/render/assetSrc.ts`). Vite bakes `import.meta.env.VITE_*` in at build
	# time from the shell's own environment, so this has to be read from the box's `.env` here,
	# on the laptop doing the build, not set anywhere on the box itself. Left unset, the resolver
	# falls back to same-origin — `/{org}/{asset}` at the bundle's own root — which is NOT where
	# Caddy proxies the bucket (`/opsgrids-public/*`), so every uploaded image would 404 on a
	# live store while the upload itself kept succeeding.
	case "$app" in
	buyer | storefront)
		asset_base=$(ssh_box "sed -n 's/^PUBLIC_ASSET_BASE_URL=//p' '$repo/infrastructure/edge/.env' | tail -n 1")
		asset_base=$(_dotenv_value "$asset_base")
		if [ -z "$asset_base" ]; then
			echo "refusing to publish: PUBLIC_ASSET_BASE_URL is empty in the box's .env." >&2
			echo "  Every uploaded image would 404 on the live store. Set it (infrastructure/edge/.env.example)" >&2
			echo "  and deploy before publishing $app." >&2
			exit 1
		fi
		case "$asset_base" in
		https://*) ;;
		*)
			echo "refusing to publish: PUBLIC_ASSET_BASE_URL on the box is not an https:// URL." >&2
			echo "  Check infrastructure/edge/.env on the box for a stray quote, a CRLF line" >&2
			echo "  ending, or a plain http:// value, then deploy before publishing $app." >&2
			exit 1
			;;
		esac
		export VITE_ASSET_BASE_URL="$asset_base"
		echo "building $app with node $node_major (VITE_ASSET_BASE_URL=$asset_base)"
		;;
	*)
		echo "building $app with node $node_major"
		;;
	esac

	# ADR-0003 addendum: every product SPA except the dashboard and the buyer bundle centralises
	# its silent refresh and logout against `app.opsgrids.com` — the one host the refresh cookie
	# lives on. Baked in at build time the same way `VITE_ASSET_BASE_URL` is above, and read from
	# the box's existing `APP_BASE_URL` rather than a second config key that could quietly drift
	# from it: they name the same host on purpose. `dashboard` never sets this — the cookie
	# already lives on its own origin — and `buyer` carries no auth code to configure at all.
	# `internal` (the staff console, admin.opsgrids.com) is the third exception: it authenticates
	# operators against admin-gateway's own same-origin refresh, never the tenant cookie this
	# whole block exists to centralise, so it gets neither VITE_AUTH_ORIGIN nor the
	# AUTH_REFRESH_CORS_ORIGINS check below — admin.opsgrids.com must never be added to that
	# tenant-only allow-list (see infrastructure/edge/.env.example).
	case "$app" in
	buyer | dashboard | internal) ;;
	*)
		auth_origin=$(ssh_box "sed -n 's/^APP_BASE_URL=//p' '$repo/infrastructure/edge/.env' | tail -n 1")
		auth_origin=$(_dotenv_value "$auth_origin")
		auth_origin=$(_origin_of "$auth_origin")
		if [ -z "$auth_origin" ]; then
			echo "refusing to publish: APP_BASE_URL is empty in the box's .env." >&2
			echo "  $app would build defaulting to same-origin refresh, which is wrong for every" >&2
			echo "  app but the dashboard — sign-in would never survive a reload. Set it and" >&2
			echo "  deploy before publishing $app." >&2
			exit 1
		fi
		case "$auth_origin" in
		https://*) ;;
		*)
			echo "refusing to publish: APP_BASE_URL on the box is not an https:// URL." >&2
			exit 1
			;;
		esac
		export VITE_AUTH_ORIGIN="$auth_origin"
		echo "  VITE_AUTH_ORIGIN=$auth_origin"

		# MINOR 4 of the 2026-09-24 SSO review: publishing an app whose own host is not in
		# AUTH_REFRESH_CORS_ORIGINS would ship a build that signs in fine (the /login hand-off
		# to the dashboard is a same-site navigation, not a CORS-gated fetch) and then fails
		# its very first silent refresh — the browser's own CORS check refuses the preflight,
		# and the client can only report that as its generic "error" outcome. Catch it here,
		# where the fix is a one-line edit to the box's .env, not a support ticket later.
		auth_refresh_cors_origins=$(ssh_box "sed -n 's/^AUTH_REFRESH_CORS_ORIGINS=//p' '$repo/infrastructure/edge/.env' | tail -n 1")
		auth_refresh_cors_origins=$(_dotenv_value "$auth_refresh_cors_origins")
		case ",$auth_refresh_cors_origins," in
		*",https://$app_host,"*) ;;
		*)
			echo "refusing to publish $app: https://$app_host is not in the box's" >&2
			echo "  AUTH_REFRESH_CORS_ORIGINS (infrastructure/edge/.env). Add it — comma-separated," >&2
			echo "  alongside the other product app hosts, never a storefront host — and deploy" >&2
			echo "  before publishing $app, or every session on this app will fail to survive" >&2
			echo "  a reload once its access token expires." >&2
			exit 1
			;;
		esac
		;;
	esac

	# The warehouse map (docs/superpowers/specs/2026-10-05-warehouse-map-putaway-design.md §5, §7)
	# needs a Google Maps JavaScript API key, and ONLY the operations bundle has a map: the worker
	# app takes a put-away by scanning a bin label and never draws one. So this is the one case in
	# the script that reads it, and every other app is built with the variable removed — a key a
	# maintainer happens to have exported in the laptop's own shell must not ride into a bundle
	# that never asked for it.
	#
	# It is read from the box's `.env`, over ssh, the same way as every other build-time value
	# above, so the box stays the one place the key is recorded. It is NOT a server secret: Vite
	# bakes it into the bundle and every visitor's browser receives it, which is why the Google
	# Cloud console restriction (HTTP referrer, API list, budget cap) is the real protection, not
	# this script. It is still never PRINTED here — not its value, only whether it was found.
	#
	# A missing, empty or malformed key is a warning, never a refusal: the map must be fully
	# DISABLED, never broken, so the bundle is published map-less (the sites page falls back to a
	# table, the warehouse dialog to manual coordinates) and a build log that scrolls past is the
	# only place a forgotten key would otherwise go unnoticed. The variable is exported EMPTY in
	# that case rather than left unset, so a `frontend/product-operations/.env.local` on this
	# machine cannot quietly re-enable a map the box was not given a key for.
	case "$app" in
	operations)
		maps_key=$(ssh_box "sed -n 's/^VITE_GOOGLE_MAPS_API_KEY=//p' '$repo/infrastructure/edge/.env' | tail -n 1")
		maps_key=$(_dotenv_value "$maps_key")
		if [ -z "$maps_key" ]; then
			maps_problem="is not set in the box's .env"
		elif ! printf '%s' "$maps_key" | grep -Eq '^[A-Za-z0-9_-]{20,}$'; then
			# Names the failure, never the value: a malformed key is still a key.
			maps_problem="in the box's .env does not look like a Google API key (letters, digits, - and _ only, 20 or more of them: check for a stray quote, space or CRLF)"
			maps_key=""
		fi
		if [ -n "$maps_key" ]; then
			echo "  VITE_GOOGLE_MAPS_API_KEY: found in the box's .env (${#maps_key} characters; the value is not printed)"
		else
			cat >&2 <<NOMAP
================================================================================
WARNING: VITE_GOOGLE_MAPS_API_KEY $maps_problem.
  Publishing operations WITHOUT the Google map. Nothing breaks: the sites page shows a
  table of warehouses and the warehouse dialog takes latitude/longitude by hand.
  To enable the map, add the key to infrastructure/edge/.env on the box (RUNBOOK.md,
  'Warehouse map and storage put-away') and publish operations again.
================================================================================
NOMAP
		fi
		export VITE_GOOGLE_MAPS_API_KEY="$maps_key"
		;;
	*)
		unset VITE_GOOGLE_MAPS_API_KEY
		;;
	esac
	(cd "$repo_root/frontend" && pnpm install --frozen-lockfile)
	(cd "$app_dir" && pnpm run "$build_script")

	source_dir="$app_dir/$dist_dir"
	[ -f "$source_dir/index.html" ] || {
		echo "refusing to publish: the build produced no $source_dir/index.html" >&2
		exit 1
	}
fi

# ---------------------------------------------------------------- ship and swap
# The link is RELATIVE (`releases/<stamp>`, not `$app_root/releases/<stamp>`). Caddy reads the
# web root through a bind mount at a different path (`/srv/web`), and an absolute link to the
# host's path is a dangling symlink inside the container: every store answered the edge's
# "not serving a store" page while the bundle sat published on disk. Found on the first
# end-to-end run.
echo "shipping to $release"
ssh_box "mkdir -p '$app_root/releases'"
rsync -az --delete -e 'ssh -o BatchMode=yes' "$source_dir/" "$host:$release/"

ssh_box "set -euo pipefail
  test -f '$release/index.html'
  ln -sfn 'releases/$stamp' '$app_root/current.tmp'
  mv -T '$app_root/current.tmp' '$app_root/current'
  echo \"current -> \$(readlink -f '$app_root/current')\"
  # Keep the five most recent releases. Rolling back needs at least two; keeping everything is
  # how a small disk fills up without anyone deciding to fill it.
  cd '$app_root/releases' && ls -1 | sort | head -n -5 | xargs -r rm -rf"

echo
echo "published $app. Roll back with: make edge-web-rollback APP=$app HOST=$host CONFIRM=$confirm"
