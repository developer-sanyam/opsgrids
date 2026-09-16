"""Point a membership at the employment record that belongs to it. Run inside `services/account`.

**This seeder exists because the platform has no other way to do it, and that is a finding, not
a convenience.** `account.memberships.employee_id` is what auth-service reads to mint the `sref`
claim (`auth/app/services.py`: `subject_ref=membership.employee_id`), which the gateway forwards
as `X-Subject-Ref` and every OWN-scoped check in the estate resolves against. Without it a real
token carries no subject reference at all, and the consequences are silent rather than loud:

* attendance's `check_in` cannot name the punching employee;
* approval's `GET /inbox` returns an empty page by design — "a membership with no employee link
  can decide nothing" (ADR-0006 §6 rule 2) — so an approver simply sees nothing, with no error;
* `POST /delegations` answers 403 "No employee link to create a delegation".

Searched for at the time of writing: **nothing in `services/` ever writes that column.**
employee-service accepts a `membership_id` on the employment record and stores it on its own
side, but never calls back into account to complete the other half of the link, and account
exposes no route — public or internal — that sets it. So the join exists in the data model, is
read on every sign-in, and has no writer.

The precedent for repairing it here is `_seed_account.py`'s `is_owner`, which is the same shape
of gap and is repaired by `tools/demo/promote_owner.py` in the demo stack. This does the same for
the employee link, through the real model on the admin role, and the journey then proves the
*consequence* over HTTP: the member signs in again and their token carries `sref`.

Usage: `_link_employee.py <service_dir> <membership_id>=<employee_id> [...]`
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
PAIRS = [tuple(arg.split("=", 1)) for arg in sys.argv[2:]]

sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings  # noqa: E402
from app.models import SCHEMA  # noqa: E402


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )

    async with engine.begin() as conn:
        for membership_id, employee_id in PAIRS:
            result = await conn.execute(
                text(
                    f'UPDATE "{SCHEMA}"."memberships" '  # noqa: S608
                    "SET employee_id = :employee_id WHERE id = :membership_id"
                ),
                {
                    "employee_id": uuid.UUID(employee_id),
                    "membership_id": uuid.UUID(membership_id),
                },
            )
            if result.rowcount != 1:
                raise SystemExit(
                    f"membership {membership_id} did not exist — the link would have been "
                    "silently skipped and every OWN-scoped assertion after it would fail for "
                    "the wrong reason"
                )

    await engine.dispose()


asyncio.run(main())
