#!/usr/bin/env bash
# upgrade head → downgrade base → upgrade head, for the service in the working directory.
#
# Its own script because `fan-out.sh` takes one command, and chaining three through a shell
# argument would mean quoting `&&` past both make and bash. The assertions are exactly what
# the previous inline loop ran: all the way up, all the way down, all the way up again.
#
# `downgrade base` drops every table the service owns, so it says, in the environment, that
# destroying data is the point (OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1). A migration that cannot
# tell whether its rows are in use elsewhere — catalog's b88a833020dc cannot see which subscriptions
# pin its plan versions — refuses its downgrade without it. Only this step carries it; nothing that
# runs on a real box (`remote.sh migrate`, a hand-typed `alembic downgrade <rev>`) does.
set -euo pipefail
uv run alembic upgrade head
OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1 uv run alembic downgrade base
uv run alembic upgrade head
