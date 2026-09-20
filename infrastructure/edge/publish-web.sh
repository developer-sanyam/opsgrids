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
	# The Inventory product's app, ops.opsgrids.com (ADR-0013 slice 3).
	app_dir="$repo_root/frontend/product-operations"
	build_script="build"
	dist_dir="dist"
	entry="index.html"
	;;
*)
	echo "unknown APP '$app' — one of: buyer, dashboard, operations" >&2
	exit 64
	;;
esac

ssh_box() { ssh -o BatchMode=yes "$host" "$@"; }

# The same wrong-box interlock the deploy uses. Changing what people see is a deploy.
ssh_box "$repo/infrastructure/edge/remote.sh" assert-host "$confirm"

web_root=$(ssh_box "sed -n 's/^EDGE_WEB_ROOT=//p' '$repo/infrastructure/edge/.env' | head -1")
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

	echo "building $app with node $node_major"
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
