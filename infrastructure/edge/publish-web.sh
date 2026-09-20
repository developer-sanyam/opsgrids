#!/usr/bin/env bash
# Publish the buyer bundle to the edge — build here, ship there, swap a symlink.
#
# Why the bundle is not built on the box like everything else:
#
#   * The root .dockerignore excludes `frontend` and every `dist` from every build context, so
#     no service image can contain it. Un-excluding it would put the whole frontend into the
#     context of all thirty-five service builds.
#   * A static bundle is the one artefact where a failed build genuinely can leave a merchant's
#     shop broken, because there is no "old container still running" to fall back on. Building
#     on a laptop and shipping only a successful `dist-buyer/` means the box only ever receives
#     bytes that compiled.
#   * The swap is a symlink move, so publishing is atomic and rolling back is another symlink
#     move — no container restart, no downtime, no rebuild.
#
# Usage:
#   publish-web.sh publish  <host> <confirm-edge-host>     build, ship, swap
#   publish-web.sh rollback <host> <confirm-edge-host>     swap back to the previous release
#   publish-web.sh releases <host> <confirm-edge-host>     what is on the box
#
# PLACEHOLDER=1 publishes the committed "this address is not serving a store" page instead of
# a bundle. That exists so a box can be stood up and reached over HTTPS before the buyer entry
# point exists, and it is deliberately unmistakable for a merchant's store.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"

if [ $# -lt 3 ]; then
	echo "usage: publish-web.sh <publish|rollback|releases> <host> <confirm-edge-host>" >&2
	echo "  confirm must equal the EDGE_HOST recorded in the box .env" >&2
	exit 64
fi
action="$1"
host="$2"
confirm="$3"
repo="${EDGE_REPO:-/srv/opsgrids/repo}"

ssh_box() { ssh -o BatchMode=yes "$host" "$@"; }

# The same wrong-box interlock the deploy uses. Changing what buyers see is a deploy.
ssh_box "$repo/infrastructure/edge/remote.sh" assert-host "$confirm"

web_root=$(ssh_box "sed -n 's/^EDGE_WEB_ROOT=//p' '$repo/infrastructure/edge/.env' | head -1")
web_root="${web_root:-/srv/opsgrids/web}"
echo "web root on box: $web_root"

case "$action" in
releases)
	ssh_box "ls -1 '$web_root/releases' 2>/dev/null | sort; echo; echo -n 'current -> '; readlink -f '$web_root/current' 2>/dev/null || echo '(none)'"
	exit 0
	;;

rollback)
	# Pick the newest release that is not the one currently linked. Timestamped names sort
	# lexicographically because they are UTC and zero-padded.
	ssh_box "set -euo pipefail
      cd '$web_root/releases'
      current=\$(basename \"\$(readlink -f '$web_root/current')\")
      previous=\$(ls -1 | sort | grep -v \"^\$current\$\" | tail -1)
      if [ -z \"\$previous\" ]; then echo 'no previous release to roll back to' >&2; exit 1; fi
      ln -sfn \"releases/\$previous\" '$web_root/current.tmp'   # relative: see publish below
      mv -T '$web_root/current.tmp' '$web_root/current'
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
release="$web_root/releases/$stamp"

if [ "${PLACEHOLDER:-0}" = "1" ]; then
	source_dir="$here/web-placeholder"
	echo "publishing the PLACEHOLDER page from $source_dir"
else
	app="$repo_root/frontend/product-storefront"
	if [ ! -f "$app/buyer.html" ]; then
		cat >&2 <<'MISSING'
refusing to publish: frontend/product-storefront/buyer.html does not exist.

ADR-0012 §2 puts the buyer surface in a SECOND Vite entry point — buyer.html →
main.buyer.tsx → dist-buyer/ — containing no auth code at all. Until that entry exists there
is no bundle to serve, and publishing the merchant admin build in its place would ship the
auth client, the org switcher and the admin routes to every anonymous shopper.

Two honest options:
  * land the buyer entry point, then run this again;
  * or publish the placeholder so the box serves an explicit "not serving a store" page over
    HTTPS while you wait:  make edge-web HOST=... CONFIRM=... PLACEHOLDER=1
MISSING
		exit 1
	fi
	if ! grep -q '"build:buyer"' "$app/package.json"; then
		echo "refusing to publish: no 'build:buyer' script in $app/package.json." >&2
		echo "  It must build buyer.html and output dist-buyer/." >&2
		exit 1
	fi

	node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
	if [ "$node_major" -lt 22 ]; then
		echo "refusing to publish: node $node_major is on PATH; this workspace needs 22 or newer." >&2
		echo "  Vite 7 and Next refuse older runtimes. Switch with nvm before publishing." >&2
		exit 1
	fi

	echo "building the buyer bundle with node $node_major"
	(cd "$repo_root/frontend" && pnpm install --frozen-lockfile)
	(cd "$app" && pnpm run build:buyer)

	source_dir="$app/dist-buyer"
	[ -f "$source_dir/index.html" ] || {
		echo "refusing to publish: the build produced no $source_dir/index.html" >&2
		exit 1
	}
fi

# ---------------------------------------------------------------- ship and swap
echo "shipping to $release"
ssh_box "mkdir -p '$web_root/releases'"
rsync -az --delete -e 'ssh -o BatchMode=yes' "$source_dir/" "$host:$release/"

# The link is RELATIVE (`releases/<stamp>`, not `$web_root/releases/<stamp>`). Caddy reads the
# web root through a bind mount at a different path (`/srv/web`), and an absolute link to the
# host's path is a dangling symlink inside the container: every store answered the edge's
# "not serving a store" page while the bundle sat published on disk. Found on the first
# end-to-end run.
ssh_box "set -euo pipefail
  test -f '$release/index.html'
  ln -sfn 'releases/$stamp' '$web_root/current.tmp'
  mv -T '$web_root/current.tmp' '$web_root/current'
  echo \"current -> \$(readlink -f '$web_root/current')\"
  # Keep the five most recent releases. Rolling back needs at least two; keeping everything is
  # how a small disk fills up without anyone deciding to fill it.
  cd '$web_root/releases' && ls -1 | sort | head -n -5 | xargs -r rm -rf"

echo
echo "published. Roll back with: make edge-web-rollback HOST=$host CONFIRM=$confirm"
