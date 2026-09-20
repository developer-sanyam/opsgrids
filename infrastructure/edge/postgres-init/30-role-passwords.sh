#!/bin/bash
# Give every opsgrids_* role the production password. Runs once, on a fresh data volume,
# immediately after 20-init.sql — which creates the roles with the development password
# 'opsgrids' hardcoded, because that file is shared with `tools/dev-bootstrap` and parity of
# the roles themselves is worth more than a second copy of the file.
#
# Every role gets the SAME password and a DIFFERENT username. That is not laziness: each
# service rewrites only the username of DATABASE_URL onto its own role
# (`ServiceSettings.dsn_as`), so one password per role would mean one DSN per service, which
# is a deployment system. The isolation that matters — a service cannot read another's schema,
# a bypass reader can only SELECT one table — is enforced by grants, not by passwords.
#
# Safe to run again by hand to rotate: it is a loop of ALTER ROLE over whatever exists now.
#   docker compose -f compose.prod.yml exec -e OPSGRIDS_DB_PASSWORD=<new> -T postgres \
#       bash /docker-entrypoint-initdb.d/30-role-passwords.sh
# After rotating, update OPSGRIDS_DB_PASSWORD in .env and restart the services.
set -euo pipefail

if [ -z "${OPSGRIDS_DB_PASSWORD:-}" ]; then
	echo "30-role-passwords: OPSGRIDS_DB_PASSWORD is unset." >&2
	echo "  Every opsgrids_* role would keep the development password from 20-init.sql," >&2
	echo "  on a box with a public IP. Refusing to finish initialising." >&2
	exit 1
fi

case "$OPSGRIDS_DB_PASSWORD" in
*[!A-Za-z0-9]*)
	echo "30-role-passwords: OPSGRIDS_DB_PASSWORD must be alphanumeric." >&2
	echo "  It is substituted into a connection URL; '@', '/' and '#' break it in ways" >&2
	echo "  that look like an authentication failure. Use: openssl rand -hex 32" >&2
	exit 1
	;;
esac

# The password travels as a psql variable and is quoted by psql (`:'pw'`), never by string
# concatenation in this shell. **The SQL goes in on stdin, not with `-c`**: psql performs
# variable interpolation only on input it reads, and a `-c` string is sent to the server
# verbatim — which is how the first deploy sent the literal text `:"role"` and left every
# role on the development password (the init aborted, and on restart the entrypoint skipped
# initialisation altogether). `\gexec` runs each generated ALTER ROLE over one connection.
psql -v ON_ERROR_STOP=1 --quiet --no-align --tuples-only \
	--username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
	-v "pw=$OPSGRIDS_DB_PASSWORD" <<'SQL'
SELECT format('ALTER ROLE %I PASSWORD %L', rolname, :'pw')
  FROM pg_roles
 WHERE rolcanlogin AND rolname LIKE 'opsgrids\_%'
\gexec
SELECT '30-role-passwords: reset password for ' || rolname
  FROM pg_roles
 WHERE rolcanlogin AND rolname LIKE 'opsgrids\_%'
 ORDER BY rolname;
SQL
