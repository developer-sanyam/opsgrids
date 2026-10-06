#!/usr/bin/env bash
# CI only: trade a throwaway Postgres's crash safety for speed.
#
# The service fixtures TRUNCATE ~50 tables before every test, so a service suite is mostly
# many small transactions that each commit, create and delete relation files, and wait for the
# disk to say it is done. Measured against the stock postgis image on the same tests:
#
#       stock settings                           123 s
#       fsync, synchronous_commit, full_page_writes off     53 s
#       a tmpfs data directory instead                       51 s
#
# Neither the database nor its data outlives the job, so durability across a crash is worth
# nothing here and every millisecond of fsync is pure cost. Only these three are touched: all
# are reloadable, so no restart and no change to how the service container is started, and
# none changes what a query returns, only when the server stops waiting for the disk. Planner
# settings and autovacuum are deliberately left alone — tests assert on query plans.
#
# Refuses to run outside CI: turning fsync off on a database anyone cares about is how data
# gets lost. GitHub sets CI=true on every runner.
#
# usage: ci-postgres-tune.sh        (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE honoured;
#                                    the defaults are the workflow's service container)
set -euo pipefail

if [ "${CI:-}" != "true" ]; then
  echo "ci-postgres-tune: refusing to run outside CI (CI=true is not set)." >&2
  echo "  It turns fsync off, which is only acceptable on a database that is thrown away." >&2
  exit 1
fi

export PGHOST=${PGHOST:-localhost}
export PGUSER=${PGUSER:-opsgrids}
export PGPASSWORD=${PGPASSWORD:-opsgrids}
export PGDATABASE=${PGDATABASE:-opsgrids}

psql -X -q -o /dev/null -v ON_ERROR_STOP=1 <<'SQL'
ALTER SYSTEM SET fsync = off;
ALTER SYSTEM SET synchronous_commit = off;
ALTER SYSTEM SET full_page_writes = off;
SELECT pg_reload_conf();
SQL

# pg_reload_conf() only signals the postmaster; the new value is visible to a new session a
# moment later. Check rather than assume — a tuning step that silently did nothing would leave
# the pipeline slow and nobody the wiser.
for _ in $(seq 1 20); do
  if [ "$(psql -X -tA -c 'SHOW fsync')" = "off" ]; then
    echo "ci-postgres-tune: fsync=off synchronous_commit=off full_page_writes=off"
    exit 0
  fi
  sleep 0.25
done
echo "ci-postgres-tune: fsync is still on after the reload" >&2
exit 1
