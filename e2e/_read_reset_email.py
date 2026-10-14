"""Read the set-password / reset-password link the platform actually sent, off the real
delivery path — the same one `POST /api/v1/auth/password/forgot` uses in production
(`edge_lib.py::read_reset_token` does the identical thing over ssh against the
production box; this is the local-harness equivalent, a direct read instead of an ssh hop).

The token is never returned by any HTTP response — that is the whole point of a reset link
arriving by mail rather than in the API's answer — so C8's HTTP-only seeding still needs one
direct read: not of application state, but of the "inbox" itself. `notification.messages` is
that inbox: the row a real deploy would also have to look at (or MailHog's, if the transport is
`smtp` — this harness runs the `log` transport, matching production, so `messages.body` is
where the rendered email actually is).

Polls rather than reading once: the reset request is asynchronous end to end — auth-service
stages `core.user.password_reset_requested` on its own outbox, its relay has to publish it,
and notification-service's consumer has to fold it into a `messages` row and its dispatcher has
to mark it `sent` — all four hops this harness now boots for real (`conftest.py`'s
`BACKGROUND`).

Run **inside `services/notification`** as a subprocess, for the same reason as every other
seeder here: one interpreter, one service's `app`.

Usage: `_read_reset_email.py <service_dir> <to_address>`
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
TO_ADDRESS = sys.argv[2]

sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings  # noqa: E402
from app.models import SCHEMA  # noqa: E402

#: Matches `edge_lib.read_reset_token`'s pattern exactly — the same link shape, wherever it is
#: read from.
_TOKEN_RE = re.compile(r"reset-password\?token=([A-Za-z0-9_\-]+)")

ATTEMPTS = 45
INTERVAL_SECONDS = 1.0


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )

    body = ""
    async with engine.connect() as conn:
        for _ in range(ATTEMPTS):
            row = (
                await conn.execute(
                    text(
                        f'SELECT body FROM "{SCHEMA}"."messages" '  # noqa: S608
                        "WHERE to_address = :to AND state = 'sent' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"to": TO_ADDRESS},
                )
            ).first()
            if row is not None and "reset-password?token=" in row[0]:
                body = row[0]
                break
            await asyncio.sleep(INTERVAL_SECONDS)

    await engine.dispose()

    match = _TOKEN_RE.search(body)
    print(json.dumps({"token": match.group(1) if match else "", "body": body}))


asyncio.run(main())
