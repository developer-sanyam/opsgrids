#!/usr/bin/env bash
# One CI test shard: the items tools/ci_shards.py assigns it, narrowed to what a change could
# affect (tools/filter-affected.sh), run through tools/fan-out.sh with tools/pytest-item.sh as
# the per-item command.
#
# Where tools/shard-filtered.sh partitions *services*, this partitions *work items* — a whole
# service, or one part of a service too heavy for a single shard (see ci_shards.py for why
# that exists and what keeps it from dropping a test). The two emptinesses the older script
# tells apart apply here the same way: a shard the plan gave nothing is always a bug and
# fails; a shard the *filter* emptied is a deliberate "nothing affected landed here" and
# succeeds.
#
# usage: test-shard.sh <shard> <shards> <jobs>
set -euo pipefail

shard=${1:?shard}
shards=${2:?shards}
jobs=${3:?jobs}

root=$(cd "$(dirname "$0")/.." && pwd)
python=${PYTHON:-python3}

# Fail closed before running anything: a plan that drops, duplicates or double-books a test
# file is worse than no plan, and this is a few hundred milliseconds.
"$python" "$root/tools/ci_shards.py" verify "$shards"

# CI's `changes` job decided which shards exist from its own view of the tree and passed us the
# fingerprint of the plan it saw. If ours differs, the two jobs disagree about what the tests
# are, and one of them is wrong — fail rather than run a plan nobody else is running. Unset
# on a local run there is nothing to compare against; unset *in CI* means the changes job never
# produced a digest, and skipping the comparison then would switch the check off exactly when
# the thing it guards (the planner) has already failed.
if [ -n "${TEST_PLAN_DIGEST:-}" ]; then
  ours=$("$python" "$root/tools/ci_shards.py" digest "$shards")
  if [ "$ours" != "$TEST_PLAN_DIGEST" ]; then
    echo "test-shard: this job's plan ($ours) differs from the changes job's ($TEST_PLAN_DIGEST)" >&2
    exit 1
  fi
elif [ "${CI:-}" = "true" ]; then
  echo "test-shard: CI gave this job no TEST_PLAN_DIGEST — the changes job's planner produced none, so the plan cannot be checked" >&2
  exit 1
fi

pre=$("$python" "$root/tools/ci_shards.py" items "$shard" "$shards")
if [ -z "$pre" ]; then
  echo "test-shard: shard $shard/$shards was given no work — that is always a bug in tools/ci_shards.py, not an empty filter" >&2
  exit 2
fi

post=$(printf '%s\n' "$pre" | "$root/tools/filter-affected.sh")
if [ -z "$post" ]; then
  echo "test-shard: shard $shard/$shards — nothing affected landed here, nothing to do"
  exit 0
fi

printf '%s\n' "$post" | "$root/tools/fan-out.sh" --stdin "$jobs" tests ok-empty "$root/tools/pytest-item.sh"
