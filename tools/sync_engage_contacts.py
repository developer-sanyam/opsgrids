#!/usr/bin/env python3
"""Copy an organization's CRM contacts into Engage's contact book (H9, CRM sub-project 4 §7.4).

The live path is the `crm.book.contact.*` events. This tool covers what the events cannot: contacts
that existed before the handler shipped, and events lost while it was not deployed. It runs the
very same `services.project_crm_contact` the handler runs, over the state customer answers on C53
(`contacts:export`), so the result is identical and a second run changes nothing (idempotent).

**It writes no consent and never opts anyone in.** A copy is marketing-blocked until a real opt-in
is recorded, exactly like the live path. A contact with neither a usable e-mail nor a usable number
gets no copy; a deleted one has its copy removed.

Owner-run. Dry run is the default and always rolls back; `--apply` also needs `--confirm-count N`,
the "examined" figure the dry run printed for the same organizations. It prints counts only, never
a name, an address, a number or an id of a contact.

Run from the notification image (it needs that service's settings, the app database role and a
reachable customer service), one or more `--org`:

    cd services/notification
    uv run python ../../tools/sync_engage_contacts.py --org <org_id>                  # dry run
    uv run python ../../tools/sync_engage_contacts.py --org <org_id> --apply --confirm-count N

Point `DATABASE_URL` at the intended database first; in a worktree that is an isolated one, never
the shared `opsgrids`. Do not run it against production without the owner's approval of that
specific action.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class _DryRunRollback(Exception):
    """Raised inside the organization's transaction to force a rollback under a dry run."""


async def sync_organization(organization_id: uuid.UUID, *, apply: bool) -> Counter[str]:
    from app import crm_contacts, services  # type: ignore[import-not-found]
    from app.config import settings  # type: ignore[import-not-found]
    from platform_core.db import session_scope

    cfg = settings()
    # Read every page first: no transaction is open while customer is being called.
    items: list[crm_contacts.CrmContact] = []
    cursor = None
    while True:
        page = await crm_contacts.fetch_crm_contact_page(cfg, str(organization_id), cursor)
        items.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    counts: Counter[str] = Counter(examined=len(items))
    try:
        async with session_scope(organization_id=organization_id) as session:
            for item in items:
                outcome = await services.project_crm_contact(
                    session,
                    organization_id=organization_id,
                    crm_contact_id=item.contact_id,
                    state=item,
                )
                counts[outcome] += 1
            if not apply:
                raise _DryRunRollback
    except _DryRunRollback:
        pass
    return counts


async def run(organizations: list[uuid.UUID], *, apply: bool, confirm_count: int | None) -> int:
    from app.config import settings
    from platform_core.db import dispose_engine, init_engine

    init_engine(settings())
    try:
        if apply:
            # Re-count first, read-only, so a stale confirmation never writes.
            planned = 0
            for org in organizations:
                planned += (await sync_organization(org, apply=False))["examined"]
            if confirm_count != planned:
                print(f"refusing: --confirm-count {confirm_count} but {planned} would be examined")
                return 2
        total: Counter[str] = Counter()
        for org in organizations:
            counts = await sync_organization(org, apply=apply)
            total.update(counts)
            print(json.dumps({"organization_id": str(org), "mode": _mode(apply), **counts}))
        print(json.dumps({"summary": True, "mode": _mode(apply), **total}))
        return 0
    finally:
        await dispose_engine()


def _mode(apply: bool) -> str:
    return "apply" if apply else "dry-run"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--org", action="append", required=True, type=uuid.UUID, dest="orgs")
    parser.add_argument("--apply", action="store_true", help="write; the default is a dry run")
    parser.add_argument("--confirm-count", type=int, help="the dry run's 'examined' total")
    args = parser.parse_args(argv)
    if args.apply and args.confirm_count is None:
        parser.error("--apply needs --confirm-count N (the 'examined' total of a dry run)")
    sys.path.insert(0, str(ROOT / "services" / "notification"))
    return asyncio.run(run(args.orgs, apply=args.apply, confirm_count=args.confirm_count))


if __name__ == "__main__":
    raise SystemExit(main())
