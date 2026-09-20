#!/usr/bin/env bash
# The half of a deploy that runs on YOUR LAPTOP: name the box, prove you meant that box, put a
# ref on it, and hand over to the box's own remote.sh.
#
# Called by the Makefile. Every argument is required and none has a default host — "deployed to
# production by accident" is a class of incident this script exists to make impossible:
#
#   * HOST is an ssh target you type. There is no default and no fallback.
#   * CONFIRM must equal the EDGE_HOST recorded in the box's own .env. Aim at the wrong box and
#     the first ssh refuses before anything is fetched, built or restarted.
#   * REF is a git ref that must exist ON THE REMOTE. The box pulls a pushed commit; your
#     working tree is never the thing that is deployed, so uncommitted work cannot reach
#     production even by accident.
#
# Usage: ssh-deploy.sh <host> <confirm-edge-host> <ref> <remote-command...>
set -euo pipefail

usage() {
	echo "usage: ssh-deploy.sh <host> <confirm-edge-host> <ref> [remote command]" >&2
	echo "  host     an ssh target, e.g. deploy@203.0.113.7" >&2
	echo "  confirm  must equal the EDGE_HOST recorded in the box .env" >&2
	echo "  ref      a git ref that exists on the remote, e.g. origin/main" >&2
	exit 64
}

[ $# -ge 3 ] || usage
host="$1"
confirm="$2"
ref="$3"
shift 3
command=("$@")
[ ${#command[@]} -gt 0 ] || command=(deploy)

repo="${EDGE_REPO:-/srv/opsgrids/repo}"

cat <<BANNER

  ────────────────────────────────────────────────────────────────
   DEPLOYING TO A PRODUCTION BOX
     ssh target      $host
     claims to be    $confirm
     repo on box     $repo
     ref             $ref
     command         ${command[*]}
   Once a real merchant domain points here, this is their shop.
  ────────────────────────────────────────────────────────────────

BANNER

# 1. The interlock, run against the copy of remote.sh already on the box, before any state
#    changes. A box that has never been configured as an edge fails here.
ssh -o BatchMode=yes "$host" "$repo/infrastructure/edge/remote.sh" assert-host "$confirm"

# 2. Put the ref on the box. --detach, because the box is not a place anyone develops, and a
#    detached HEAD at a known sha is what makes `remote.sh status` answer honestly.
ssh -o BatchMode=yes "$host" "set -euo pipefail
  cd '$repo'
  if [ -n \"\$(git status --porcelain)\" ]; then
    echo 'refusing: the checkout on the box has local modifications.' >&2
    echo 'Someone edited files on the server. Reconcile them before deploying:' >&2
    git status --short >&2
    exit 3
  fi
  git fetch --prune --tags origin
  git checkout --detach '$ref'
  git --no-pager log -1 --format='on box: %h %ad %s' --date=short"

# 3. Hand over to the ref's own remote.sh.
ssh -o BatchMode=yes "$host" "$repo/infrastructure/edge/remote.sh" "${command[@]}"
