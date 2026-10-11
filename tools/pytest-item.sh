#!/usr/bin/env bash
# Run pytest for one test-shard item, from inside services/<service> (tools/fan-out.sh puts
# us there and names the item in FAN_OUT_ITEM).
#
# A whole-service item is exactly what `make test-services` runs — `pytest tests`. A part
# (`<service>:<part>/<parts>`) is handed the test files tools/ci_shards.py assigned it, and
# nothing else: same rootdir, same conftest, same plugins, just fewer modules.
#
# JUNIT_DIR, when set (absolute — we are not in the repository root), makes pytest write one
# junit XML per item. CI uploads them so `tools/ci_shards.py refresh` can re-balance the
# shards from real timings.
set -euo pipefail

item=${FAN_OUT_ITEM:?pytest-item.sh is run by tools/fan-out.sh, which sets FAN_OUT_ITEM}
root=$(cd "$(dirname "$0")/.." && pwd)
python=${PYTHON:-python3}

args=()
case "$item" in
*:*)
  while IFS= read -r f; do
    args+=("$f")
  done < <("$python" "$root/tools/ci_shards.py" files "$item")
  # An empty list must not fall through to a bare `pytest`, which would quietly run the whole
  # service (testpaths) — the opposite of a part.
  if [ "${#args[@]}" -eq 0 ]; then
    echo "pytest-item: ci_shards.py named no test files for $item" >&2
    exit 2
  fi
  ;;
*)
  args+=(tests)
  ;;
esac

args+=(-q -p no:cacheprovider)

if [ -n "${JUNIT_DIR:-}" ]; then
  mkdir -p "$JUNIT_DIR"
  name=${item//:/--}
  name=${name//\//of}
  args+=("--junitxml=$JUNIT_DIR/$name.xml")
fi

exec uv run pytest "${args[@]}"
