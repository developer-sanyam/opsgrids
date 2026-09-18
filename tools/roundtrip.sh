#!/usr/bin/env bash
# upgrade head → downgrade base → upgrade head, for the service in the working directory.
#
# Its own script because `fan-out.sh` takes one command, and chaining three through a shell
# argument would mean quoting `&&` past both make and bash. The assertions are exactly what
# the previous inline loop ran: all the way up, all the way down, all the way up again.
set -euo pipefail
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
