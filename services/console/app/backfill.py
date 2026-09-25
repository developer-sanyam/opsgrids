"""One-off, idempotent backfill for the projections' production gap. A one-off deploy step.

Console went live *after* every existing organization and subscription — its consumer only ever
folds the live commerce and identity event streams (`app/consumers.py`), so an organization whose
`core.org.created` and whose subscriptions' `commerce.subscription.*` transitions all happened
before this service's queue existed has nothing folded for it at all. `selectors.all_org_states`,
`.all_usage` and `.recent_audit` read real, RLS-real projections — there was simply never an event
to write one. This fills that gap once, for every organization that predates the queue, and does
nothing to one a real event has already reached.

**Never a cross-schema read.** Both account-service's and subscription-service's schemas stay
behind their own service, exactly as `directory_client.py`/`subscription_client.py`/
`catalog_client.py` already reach them for the live API: a short-lived service token, addressed by
name, over HTTP (ADR-0000 §10). `app/account_client.py` is the one new client this needs — a bulk,
paginated discovery read subscription-service already had no equivalent gap for (`GET
/internal/v1/organizations/{id}/subscriptions` already existed; this only iterates it once per
organization).

**Folds through the same fold functions the consumer uses**, not a second implementation of the
arithmetic: `services.backfill_org_identity` and `services.backfill_subscription_snapshot` share
`domain.apply_org_identity`/`domain.apply_subscription_transition` and the snapshot recompute with
`services.fold_org_event`/`fold_subscription_event`. The one difference is what each is stamped
with — a live fold uses the event's own `occurred_at`; this uses **the source row's own
`updated_at`** (account's `Organization.updated_at`, subscription's `Subscription.updated_at`).
That is what makes both directions of idempotency free instead of bespoke:

* **re-running is a no-op** — a second run reads the same `updated_at` it wrote last time, and the
  ordering guard (`domain._is_stale`, shared by both fold paths) treats "not advancing" as stale
  and drops it, untouched;
* **a later real event always wins** — an event's `occurred_at` is always later than the snapshot's
  `updated_at` that predated it (it is what caused the source row's next `updated_at`), so the
  guard lets it through the same way it lets any newer transition through.

**Emits nothing.** No outbox row (`backfill_org_identity`/`backfill_subscription_snapshot` write
only the projection, never `RevenueEvent` or `OutboxEvent` — see the latter's own docstring for
why a synthetic revenue-log row per run would be wrong), so notification — which only ever
consumes commerce events — never sees a reason to send a trial email or anything else for a
snapshot this script wrote quietly into the past.

Connection discipline mirrors subscription-service's own `app/backfill.py`: **discovery** (which
organizations exist, and what each currently holds) is a plain HTTP read with no tenant to bind —
it does not touch this service's own database at all. **Every write** runs on the NOBYPASSRLS app
role inside `session_scope(organization_id=...)`, one transaction per organization, so an
organization's identity fold and every one of its subscription folds commit together or not at
all, and a fold can never write another organization's row (ADR-0002 §2).

Usage, from `services/console`, once account-service and subscription-service are deployed:

    uv run python -m app.backfill [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import account_client, domain, services, subscription_client
from app.config import Settings
from app.price import PriceResolver
from platform_core.logging import get_logger

log = get_logger("console.backfill")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own so the backfill runs without a process-wide engine — the same shape
#: `app.consumers.WorkScope` and subscription-service's own backfill use.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class BackfillReport:
    """What one run did. Printed as JSON so the deploy log is the evidence."""

    orgs_seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 timestamp from a JSON response body. Both account's and subscription's
    `updated_at` are always present and always timezone-aware (`TimestampMixin`'s
    `DateTime(timezone=True)`), so this does not need `_parse_dt`'s own `None`-tolerant twin in
    `services.py` — a source row with no `updated_at` at all would be a contract break worth
    failing loudly on, not silently defaulting from."""
    return datetime.fromisoformat(value)


async def _backfill_one_organization(
    session: AsyncSession,
    org: dict[str, Any],
    subscriptions: list[dict[str, Any]],
    *,
    resolve_price: PriceResolver,
) -> bool:
    """Fold one organization's identity and every one of its subscriptions. Returns whether
    anything changed, for the caller's created/updated/unchanged bucketing."""
    organization_id = uuid.UUID(str(org["id"]))
    org_updated_at = _parse_dt(str(org["updated_at"]))

    changed = await services.backfill_org_identity(
        session,
        organization_id=organization_id,
        name=str(org["name"]),
        slug=str(org["slug"]),
        source_updated_at=org_updated_at,
        country=org.get("country"),
        org_created_at=_parse_dt(str(org["created_at"])) if org.get("created_at") else None,
    )

    for sub in subscriptions:
        plan_version_id = sub.get("plan_version_id")
        state = str(sub["state"])
        # Mirrors `fold_subscription_event`'s own initial default exactly (`app/services.py`):
        # only a state that could pay or entitle is worth asking catalog to price, and an
        # unresolved or uninteresting state contributes `(0, None)` — never the subscription's
        # own `currency` field, so a backfilled snapshot is byte-identical to what the live fold
        # would have written for the same transition.
        amount_minor, currency = 0, None
        if plan_version_id and state in domain.ENTITLED_STATES:
            amount_minor, currency = await resolve_price(str(plan_version_id))
        sub_changed = await services.backfill_subscription_snapshot(
            session,
            organization_id=organization_id,
            product_key=str(sub["product_key"]),
            plan_version_id=str(plan_version_id) if plan_version_id else None,
            state=state,
            trial_ends_at=sub.get("trial_ends_at"),
            amount_minor=amount_minor,
            currency=currency,
            source_updated_at=_parse_dt(str(sub["updated_at"])),
        )
        changed = changed or sub_changed

    return changed


async def backfill(
    *,
    settings: Settings,
    work_scope: WorkScope,
    resolve_price: PriceResolver,
    client: httpx.AsyncClient,
    dry_run: bool = False,
) -> BackfillReport:
    """Discover every organization from account-service, fold its identity and its live
    subscriptions from subscription-service, one tenant-bound transaction per organization."""
    from app import selectors

    report = BackfillReport()
    organizations = await account_client.list_organizations(settings, client=client)
    for org in organizations:
        report.orgs_seen += 1
        organization_id = uuid.UUID(str(org["id"]))
        try:
            subscriptions = await subscription_client.list_subscriptions(
                settings, organization_id, client=client
            )
            async with work_scope(organization_id) as session:
                existing = await selectors.get_org_state(session, organization_id)
                existed_before = existing is not None
                if dry_run:
                    log.info(
                        "console.backfill.would_write",
                        organization_id=str(organization_id),
                        subscriptions=len(subscriptions),
                    )
                    continue
                changed = await _backfill_one_organization(
                    session, org, subscriptions, resolve_price=resolve_price
                )
            if not existed_before:
                report.created += 1
            elif changed:
                report.updated += 1
            else:
                report.unchanged += 1
        except Exception as exc:  # one bad organization must not strand the rest of the fleet
            report.failed += 1
            log.error(
                "console.backfill.failed",
                organization_id=str(organization_id),
                error=str(exc)[:300],
            )
    return report


async def main() -> None:
    from app.config import settings as settings_factory
    from app.models import SCHEMA
    from app.price import make_resolver
    from platform_core.db import assert_app_role, dispose_engine, init_engine, session_scope

    parser = argparse.ArgumentParser(
        description="Fold every org's identity and subscriptions that predate console's queue."
    )
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    cfg = settings_factory()
    engine = init_engine(cfg)
    # Writes run on the app role under a bound tenant; started on a BYPASSRLS DSN by mistake,
    # row-level security would be silently inert (ADR-0002) — same guard subscription-service's
    # own backfill and every consumer entrypoint use.
    await assert_app_role(engine, environment=cfg.environment)
    resolve_price = make_resolver(cfg)
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            report = await backfill(
                settings=cfg,
                work_scope=lambda organization_id: session_scope(organization_id=organization_id),
                resolve_price=resolve_price,
                client=client,
                dry_run=args.dry_run,
            )
        finally:
            await dispose_engine()
    print(json.dumps({"dry_run": args.dry_run, "schema": SCHEMA, **asdict(report)}))  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
