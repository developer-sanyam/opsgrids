#!/usr/bin/env bash
# upgrade head → downgrade base → upgrade head, for the service in the working directory.
#
# Its own script because `fan-out.sh` takes one command, and chaining three through a shell
# argument would mean quoting `&&` past both make and bash. The assertions are exactly what
# the previous inline loop ran: all the way up, all the way down, all the way up again.
#
# Catalog's b88a833020dc cannot tell whether a subscription pins its list-price versions, so its
# downgrade refuses whenever they exist — on every database the seeds ran on, fresh ones too —
# unless OPSGRIDS_ALLOW_LIST_PRICE_DOWNGRADE=1. `downgrade base` drops the schema anyway, so this
# step passes that one switch, which no other migration reads. It deliberately does NOT pass the
# shared OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE: that is what stops operations' downgrades from
# dropping real lots, rolls and put-away captures, and a roundtrip run against a database holding
# them must still be refused. Nothing that runs on a real box (`remote.sh migrate`, a hand-typed
# `alembic downgrade <rev>`) sets either.
set -euo pipefail
uv run alembic upgrade head
OPSGRIDS_ALLOW_LIST_PRICE_DOWNGRADE=1 uv run alembic downgrade base
uv run alembic upgrade head
