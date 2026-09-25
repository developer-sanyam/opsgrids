#!/usr/bin/env python3
"""Reach a permission added to an existing role template out to organizations that
already exist — the operation ADR-0007 exists to name.

`install_registration` upserts: a role template whose code is new installs correctly into
a live tenant the moment the product is activated or re-activated, but a role template
whose code **already exists** used to be skipped outright, so a permission added to
`MEMBER` or `ORG_OWNER` (or any other system role) reached a brand-new organization and no
existing one, forever, with no error anywhere. This is the one-shot, reviewed deploy step
that closes that gap: it widens every existing system role template names — union only,
nothing is ever removed or narrowed (ADR-0007 §1) — and reports what a customer-authored
role of the same code was denied, rather than closing that gap automatically (§4).

**And it grants a *new* admin role template to the owners who need it.** A role template
whose code is new installs correctly, but until now nothing granted it to anybody in a live
tenant — only `install_product_registration`, the activation-time path, ever did that. So
when a product's catalogue grew a new admin-most role (CRM gaining `CRM_LEADS_ADMIN` is the
production case this closes), every organization that had activated the product *before*
that template existed got the role installed with no holder, and the owner could not even
assign it to themselves — the anti-escalation rule refuses to hand out a permission the
grantor does not already hold. This reconcile now grants every one of the manifest's
`admin_role_codes` to an organization's active owners, for exactly the organizations where
the product is currently active, through `account.services.grant_admin_roles_to_owners` —
the same function `install_product_registration` calls, so the two paths cannot drift apart
again the way they did here. Union-only applies to this too: an owner already holding the
role is skipped, and nothing is ever revoked.

**Where "currently active" comes from.** account keeps no local record of product
activation — `activations` lives entirely in product-service's own schema, and reading
across that boundary is exactly what CLAUDE.md's "no cross-schema foreign keys" rule exists
to keep out. `install_product_registration`'s callers never had to ask, because each of them
*is* the fact: the consumer only runs when `core.product.activated` (or an entitling
subscription event) says so, and `tools/wire_product.py` is an operator asserting it by hand.
This command has no such trigger — it runs against a manifest, not an activation — so it asks
the one source those callers ultimately trace back to: product-service's internal
entitlements read (`GET /internal/v1/organizations/{id}/entitlements`), the same one
auth-service's `HttpEntitlements` (`services/auth/app/entitlements.py`) trusts to mint the
`ent` claim that the gateway's 402 gate rests on. Unlike that client, a failure here is not
degraded to "not active" — that would silently re-create the exact bug this closes — it
fails the organization loudly, the same as an unreachable Redis already does below.

Same manifest (`libs/contracts/permissions/<product>.json`), same installer
(`account.services.install_registration`, wrapped here by `reconcile_registration` so the
`pv` bump, the audit row and the `core.grants.changed` event only fire on a real diff), same
connection discipline as `tools/wire_product.py`: the NOBYPASSRLS app role, the tenant GUC
bound per transaction, **one transaction per organization**. Unlike `wire_product.py` this
is not driven by an activation — `core` has none, and every other product's manifest can
change independently of activation too — so it takes a product key and a fleet, not one
organization by hand.

Deliberately **not** an API: it writes to every tenant in the fleet, and a cross-tenant
write does not belong on a customer-facing surface (ADR-0007, "What this rejects"). It is
also deliberately **not** driven by a consumer or run at service startup — an unreviewed
write to every tenant on every restart, and it would strand a rolled-back service's grants
behind with nothing enforcing them.

Usage, from the repository root:

    uv run python tools/reconcile_permissions.py <product_key> --org <org_id> [--dry-run]
    uv run python tools/reconcile_permissions.py <product_key> --all [--dry-run] [--pace 250]

`--dry-run` computes and prints the same diff, per organization, and writes nothing — not
even the roles and permission rows a real run would upsert, or the admin-role grants an
active owner would pick up. It is the expected first invocation, and its output is the
evidence a PR description points to (ADR-0007 §3).

Point `DATABASE_URL` at the intended database before running — in a worktree that is
`opsgrids_wt`, never the shared `opsgrids`. Redis must be reachable: the permission-version
bump refuses to be lost, and this tool refuses to be lost with it — a Redis outage fails the
organization it happened on loudly, leaves every organization already processed committed,
and never reports the failed one as reconciled (ADR-0007 §5). `PRODUCT_SERVICE_URL` must be
reachable too, for the same reason: this tool would rather stop than guess at activation.
Re-running is safe: a clean pass computes an empty diff and changes nothing.

`--all` is deliberately sequential with a pause between organizations (`--pace`, default
250ms) rather than parallel: bumping `pv` invalidates every access token in that
organization, so a fleet run is a token-refresh burst, and pacing spreads those bursts
instead of stacking them (ADR-0007 §5). Run it outside peak hours like any other fleet
write.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Protocol

import httpx

ROOT = Path(__file__).resolve().parent.parent

#: The one place this tool departs from a hand-rolled write: it never reports an
#: organization as reconciled unless its own transaction actually committed. Also the `iss`
#: on the service token minted for product-service's entitlements read below — product's
#: internal API carries no issuer allowlist for that route, but naming this command rather
#: than defaulting to account's own service name is what makes product-service's logs of the
#: call legible.
ACTING_AS = "reconcile-permissions-cli"

#: The audience `platform_core.internal_auth.verify_service_token` checks against —
#: product-service's own `settings.service_name`.
PRODUCT_AUDIENCE = "product"


class ActivationChecker(Protocol):
    """Whether `product_key` is currently active for one organization — what decides
    whether this reconcile is allowed to grant a product's admin role to that organization's
    owners. A test substitutes a stub; see the module docstring for why the real
    implementation below asks product-service rather than guessing."""

    async def is_active(self, organization_id: uuid.UUID, product_key: str) -> bool: ...


class HttpActivationChecker:
    """The real check: product-service's internal entitlements read.

    Deliberately **not** degraded on failure the way `auth`'s own client is — that client's
    failure mode (sign in with fewer entitlements than the customer actually has) is meant
    to be the *safe* direction to be wrong in for a login; treating an unreachable
    product-service as "not active" here would be the *unsafe* direction, because it would
    silently skip the owner grant this tool exists to stop skipping. `response.raise_for_status`
    is left to raise straight through: the fleet loop stops at the first organization whose
    reconcile raises, exactly as it already does for a poisoned Redis.
    """

    def __init__(self, base_url: str, settings: Any, *, timeout: float = 5.0) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 2.0)),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def is_active(self, organization_id: uuid.UUID, product_key: str) -> bool:
        from platform_core.internal_auth import mint_service_token

        headers = {
            # Minted per call, addressed to product-service, good for seconds.
            "X-Internal-Token": mint_service_token(
                self._settings, audience=PRODUCT_AUDIENCE, caller=ACTING_AS
            ),
            "X-Calling-Service": ACTING_AS,
        }
        response = await self._client.get(
            f"/internal/v1/organizations/{organization_id}/entitlements", headers=headers
        )
        response.raise_for_status()
        entitlements = response.json().get("entitlements", [])
        return product_key in entitlements


class _DryRunRollback(Exception):
    """Raised inside an organization's transaction to force a rollback under `--dry-run`.

    Not an error — caught by the caller. Raising rather than calling `session.rollback()`
    directly and letting the `async with session.begin()` block exit normally is what
    guarantees the rollback: SQLAlchemy commits on a clean exit, so the only reliable way
    to make a transaction opened by a context manager end in a rollback is to leave it via
    an exception.
    """


def _summary_line(
    org_id: uuid.UUID,
    result: Any,
    *,
    dry_run: bool,
    skipped_customer_roles: list[str],
    product_active: bool,
) -> dict[str, Any]:
    # `admin_grant.granted` is a `(membership_id, role_code)` pair per row this run wrote (or,
    # under `--dry-run`, would have written) — grouped here by owner so the printed line answers
    # ADR-0007's own question in as many words: which owners would be granted which roles.
    owners_granted: dict[str, list[str]] = {}
    for membership_id, role_code in result.admin_grant.granted:
        owners_granted.setdefault(str(membership_id), []).append(role_code)
    return {
        "org_id": str(org_id),
        "dry_run": dry_run,
        "product_active": product_active,
        "changed": result.changed,
        "roles_created": result.roles_created,
        "grants_added": result.grants_added,
        "scopes_widened": result.scopes_widened,
        "skipped_customer_roles": skipped_customer_roles,
        "memberships_without": result.memberships_without,
        "owners_granted": [
            {"membership_id": membership_id, "roles": roles}
            for membership_id, roles in owners_granted.items()
        ],
    }


async def reconcile_organization(
    org_id: uuid.UUID, manifest: Any, checker: ActivationChecker, *, dry_run: bool
) -> dict[str, Any]:
    """One organization, one transaction (ADR-0007 §3).

    The activation check runs **before** the transaction opens, the same discipline
    `tools/reconcile_membership_links.py` uses for its own HTTP read: an HTTP call has no
    business holding a database transaction open, and none of what follows needs one — the
    fact does not change mid-transaction, and if the request fails the organization is never
    entered at all.

    A clean exit commits; under `--dry-run` the transaction is always rolled back, whether
    or not the diff was empty, so a review run can never leave a partial write behind.
    """
    from app import services  # type: ignore[import-not-found]  # loaded from the service dir
    from platform_core.db import get_sessionmaker, set_tenant

    product_active = await checker.is_active(org_id, manifest.product)

    sessionmaker = get_sessionmaker()
    result = None
    try:
        async with sessionmaker() as session, session.begin():
            await set_tenant(session, org_id)
            result = await services.reconcile_registration(
                session,
                org_id,
                manifest.registration,
                acting_as=ACTING_AS,
                admin_role_codes=manifest.admin_role_codes,
                product_active=product_active,
                dry_run=dry_run,
            )
            if dry_run:
                raise _DryRunRollback()
    except _DryRunRollback:
        pass
    assert result is not None
    return _summary_line(
        org_id,
        result,
        dry_run=dry_run,
        skipped_customer_roles=result.skipped_customer_roles,
        product_active=product_active,
    )


async def reconcile_fleet(
    product_key: str,
    org_ids: list[uuid.UUID],
    *,
    checker: ActivationChecker,
    dry_run: bool,
    pace_ms: int = 250,
) -> list[dict[str, Any]]:
    """Reconcile every organization in `org_ids` against `product_key`'s manifest.

    Sequential, one organization at a time, each its own transaction, paced between
    organizations (ADR-0007 §5). **Stops at the first organization whose reconcile
    raises** — Redis or product-service unreachable, most likely — rather than catching it
    and moving on: organizations already processed are already committed and stay that way,
    the failing one and everything after it are simply never attempted, and the next run
    picks up exactly where this one stopped, because a clean diff is idempotent and an
    unclean one is retried from scratch.
    """
    from app.catalogue import load_product_manifest  # type: ignore[import-not-found]

    manifest = load_product_manifest(product_key)
    if manifest is None:
        raise SystemExit(
            f"no permission manifest for {product_key!r} in libs/contracts/permissions/ — "
            "run `tools/export_permissions.py` first (step 1 of ADR-0007 §3's ordering); "
            "reconciling before the manifest exists writes roles missing exactly the "
            "grants this run is for, and reports success"
        )

    results: list[dict[str, Any]] = []
    for index, org_id in enumerate(org_ids):
        results.append(await reconcile_organization(org_id, manifest, checker, dry_run=dry_run))
        is_last = index == len(org_ids) - 1
        if not is_last and pace_ms > 0:
            await asyncio.sleep(pace_ms / 1000)
    return results


async def _all_organization_ids() -> list[uuid.UUID]:
    """Every live organization, read on the directory (BYPASSRLS, SELECT-only) engine.

    `organizations` carries row-level security keyed on its own `id`, so no tenant-bound
    session can ever enumerate more than the one organization it is bound to — discovering
    the fleet is, by construction, a platform operation. That does not require
    `cfg.admin_dsn` — BYPASSRLS *and* owner of every schema — for a read of one column of one
    table: `cfg.directory_dsn` is the SELECT-only BYPASSRLS role built for exactly this shape
    of cross-tenant read, already granted `SELECT` on `organizations` by
    `apply_schema_grants` (`libs/platform-core/src/platform_core/db.py`), run from every
    service's own migrations. This engine is opened and disposed for exactly this one read;
    every write that follows reconnects through the ordinary NOBYPASSRLS app role, one
    organization at a time — the same connection discipline `tools/wire_product.py` uses for
    its own write, which opens no admin engine at all.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]
    from app.models import Organization  # type: ignore[import-not-found]

    cfg = settings()
    engine = create_async_engine(cfg.directory_dsn)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            rows = await session.execute(
                select(Organization.id)
                .where(Organization.deleted_at.is_(None))
                .order_by(Organization.created_at)
            )
            return [row[0] for row in rows.all()]
    finally:
        await engine.dispose()


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Widen an existing organization's system roles to match a product's "
        "committed permission manifest (ADR-0007)"
    )
    parser.add_argument("product_key")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--org", type=uuid.UUID, help="reconcile exactly one organization")
    target.add_argument(
        "--all", action="store_true", help="reconcile every organization in the fleet"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the diff each organization would get; write nothing",
    )
    parser.add_argument(
        "--pace",
        type=int,
        default=250,
        metavar="MS",
        help="pause between organizations under --all, in milliseconds (default 250)",
    )
    args = parser.parse_args()

    # The account service directory becomes the import root, exactly as its own processes
    # run: every service exposes a top-level `app`, so one process holds exactly one.
    sys.path.insert(0, str(ROOT / "services" / "account"))

    from app.config import settings
    from platform_core.cache import close_redis, init_redis
    from platform_core.db import dispose_engine, init_engine

    async def _run() -> None:
        cfg = settings()
        init_engine(cfg)
        # Redis is a hard dependency of every reconcile that changes anything: the bump
        # raises rather than degrading, and this process must be able to raise it.
        init_redis(cfg.redis_url)
        # The activation source (see the module docstring): asked once per organization,
        # before that organization's transaction opens.
        checker = HttpActivationChecker(cfg.product_service_url, cfg)

        try:
            org_ids = [args.org] if args.org else await _all_organization_ids()
            if not org_ids:
                print(json.dumps({"product": args.product_key, "organizations": 0}))
                return

            results = await reconcile_fleet(
                args.product_key,
                org_ids,
                checker=checker,
                dry_run=args.dry_run,
                pace_ms=args.pace,
            )
        finally:
            await checker.aclose()
            await dispose_engine()
            await close_redis()

        for line in results:
            print(json.dumps(line))

        changed = sum(1 for r in results if r["changed"])
        skipped_roles = sorted({code for r in results for code in r["skipped_customer_roles"]})
        memberships_without_total = sum(r["memberships_without"] for r in results)
        owners_granted_total = sum(len(r["owners_granted"]) for r in results)
        print(
            json.dumps(
                {
                    "product": args.product_key,
                    "dry_run": args.dry_run,
                    "organizations": len(results),
                    "changed": changed,
                    "unchanged": len(results) - changed,
                    "customer_authored_role_codes_skipped": skipped_roles,
                    "memberships_without_total": memberships_without_total,
                    "owners_granted_total": owners_granted_total,
                }
            )
        )

    asyncio.run(_run())


if __name__ == "__main__":
    _main()
