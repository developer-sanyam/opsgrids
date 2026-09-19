#!/usr/bin/env bash
# Print the services belonging to one shard, balanced by measured test duration.
#
# CI runs on 2-vCPU runners, where running two of anything at once made each three times
# slower and the whole step lose. The way to buy parallelism on hardware that small is
# another runner, not another process — so the per-service work is split across jobs, each
# with its own machine and its own database.
#
# Assignment is longest-processing-time-first: sort the services by weight descending, then
# give each to whichever shard is currently lightest. That is the standard greedy bound for
# this problem and it is deterministic — same tree, same weights, same answer — so a rerun
# of shard 2 runs exactly what it ran before.
#
# It replaced `position mod shards`, which was deterministic too and badly unbalanced:
# finance (112s) and account (72s) landed together while another shard held eleven services
# totalling under a minute. Measured in CI, that shard took 154s against 56s and 51s.
#
# The list is partitioned over **every** service directory, not a filtered one. Sharding a
# filtered list is a trap already walked into: 33 services have `tests/` and 31 have
# `alembic.ini`, so filtering first put the same service in different shards for migrations
# and for tests, and a test shard then ran against schemas no shard had migrated. Partition
# once over everything; let the consumer skip what it cannot use.
#
# usage: shard.sh <index> <shards>
#
#   index     1-based shard number
#   shards    how many shards in total

set -euo pipefail

index=${1:?index}
shards=${2:?shards}

root=$(cd "$(dirname "$0")/.." && pwd)
weights="$root/tools/shard-weights.txt"

if [ "$index" -lt 1 ] || [ "$index" -gt "$shards" ]; then
  echo "shard.sh: index $index outside 1..$shards" >&2
  exit 2
fi

# The whole assignment is one awk program because bash 3.2 — which macOS still ships — has
# no associative arrays, and a weights lookup without them is not worth reading.
for dir in "$root"/services/*/; do basename "$dir"; done | awk \
  -v shards="$shards" -v want="$index" -v weights_file="$weights" '
  BEGIN {
    # Default for a service nobody has measured. Deliberately small: a new service is
    # light until proven otherwise, and shard-verify guarantees it still runs somewhere.
    default_weight = 10
    while ((getline line < weights_file) > 0) {
      if (line ~ /^[ \t]*#/ || line ~ /^[ \t]*$/) continue
      split(line, f, " ")
      weight[f[1]] = f[2]
    }
  }
  { name[NR] = $0; w[NR] = (($0) in weight) ? weight[$0] : default_weight; n = NR }
  END {
    # Selection sort by weight descending, name ascending. n is thirty-odd; clarity beats
    # cleverness here, and the tie-break on name is what keeps the result stable.
    for (i = 1; i <= n; i++) {
      pick = i
      for (j = i + 1; j <= n; j++) {
        if (w[j] > w[pick] || (w[j] == w[pick] && name[j] < name[pick])) pick = j
      }
      tw = w[i]; w[i] = w[pick]; w[pick] = tw
      tn = name[i]; name[i] = name[pick]; name[pick] = tn
    }

    for (s = 1; s <= shards; s++) load[s] = 0

    for (i = 1; i <= n; i++) {
      lightest = 1
      for (s = 2; s <= shards; s++) if (load[s] < load[lightest]) lightest = s
      load[lightest] += w[i]
      if (lightest == want) print name[i]
    }
  }'
