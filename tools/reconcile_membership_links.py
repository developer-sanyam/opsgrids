#!/usr/bin/env python3
"""Fill `account.memberships.employee_id` from the only record of anybody's intent that
exists in the estate, and verify what is already there — ADR-0008 §8.

`employee_id` is the sole origin of the `sref` claim every OWN-scoped check in ten services
resolves against, and until ADR-0008 nothing in the platform could write it. ADR-0008 makes
account its one writer (`PATCH /api/v1/members/{membership_id}`); this is the one-shot,
reviewed command that reaches the tenants that already exist, in ADR-0007 §3's shape,
discipline and vocabulary.

**What it keys on.** `employee.employees.membership_id` where it is not null — the only
statement of intent anybody ever recorded. Nothing else exists to key on. It is read across
the schema boundary over HTTP, through employee-service's
`GET /internal/v1/employee-links?organization_id=&cursor=&limit=`, which returns two UUIDs
per row and no PII. The page carries **every** employee of the organization, linked or not:
the non-null `membership_id` rows are the fill half, and the full set of `employee_id`s is
what makes the verify half meaningful — an id account holds that appears in no page is an id
employee-service does not recognise. Two costs worth knowing before a fleet run: the walk
terminates on a null `next_cursor` (a full page still carries one), and employee-service
writes **one audit row per page** in that tenant, naming this command's `iss` and the row
count. **Why not read both schemas from one process:** no database role can.
Each service connects as a NOBYPASSRLS role confined to its own schema and the only role
that spans them is `opsgrids_admin` (BYPASSRLS, owner of every schema); widening a bypass
surface for a one-time job is the wrong trade (ADR-0002 §2) — and this one *writes*.

**Which DSN, and why.** Two, each the narrowest that does its job, and `admin_dsn` is used
for neither:

* the fleet enumeration under `--all` reads one column of `organizations` on
  `cfg.directory_dsn` — the SELECT-only BYPASSRLS role. `organizations` carries RLS keyed on
  its own `id`, so no tenant-bound session can enumerate the fleet; discovering it is by
  construction a platform read, and a read is all it is. This is the same correction
  `tools/reconcile_permissions.py` took from `admin_dsn` to `directory_dsn`.
* **every write goes through the ordinary NOBYPASSRLS app role** (`init_engine(cfg)` →
  `cfg.app_dsn`), one organization at a time, with the tenant GUC bound per transaction.
  This tool writes, which is the reason to be *stricter* here rather than looser: running
  the write as `opsgrids_directory` is impossible (SELECT-only) and running it as
  `opsgrids_admin` would put a cross-tenant fleet write on a BYPASSRLS role, so a bug in the
  loop below could not be caught by the tenant policy that exists to catch it. Under the app
  role, a row this command should not be able to reach is refused by the database.

**Three rules from ADR-0008 §8 that this command does not get to bend**, all enforced in
`account.app.services.reconcile_employee_links`:

1. **It only ever fills a null.** A link account already holds is never overwritten, in
   either direction, by any run. That is what makes this re-runnable, safe alongside live
   traffic, safe to interrupt, and what removes the need for a version gate (§6).
2. **It fills and verifies in one pass.** The page carries every employee, linked or not, so
   an `employee_id` account holds that does not appear in it is an id employee-service does
   not recognise — ADR-0008 §4's mistyped UUID, and this is the only mechanism that finds it.
3. **It never bumps `pv`.** Every write it makes is grant-shaped, and §7 says grant-shaped
   changes wait out the access token. A fleet run must not be a fleet-wide refresh storm.

Usage, from the repository root:

    uv run python tools/reconcile_membership_links.py --org <org_id> [--dry-run]
    uv run python tools/reconcile_membership_links.py --all [--dry-run] [--pace 250]

`--dry-run` is the expected first invocation. It computes and prints the same report per
organization and **always rolls back** — whether or not anything was found — so a review run
can never leave a partial write behind.

**On this repository the expected result is `links_written: 0`.** Nothing in `frontend/`,
`tools/` or the seeds has ever sent `membership_id` on employee create, so a non-zero result
is not a success — it is a finding, and the summary says so in as many words: something
outside this repository was writing a field ADR-0008 §3 has now frozen.

Point `DATABASE_URL` at the intended database before running — in a worktree that is
`opsgrids_wt`, never the shared `opsgrids`. Redis is **not** required: this command bumps no
permission version, which is the one substantive way its failure contract differs from
`tools/reconcile_permissions.py`.

`--all` is sequential with a pause between organizations (`--pace`, default 250 ms) and
100 ms between pages, for the reason ADR-0006 §2.1 paces the hierarchy backfill: the load
this puts on employee-service and on the database should be spread, not stacked. It **stops
at the first organization that raises** — organizations already processed stay committed,
the failing one and everything after it are simply never attempted, and the next run picks up
exactly where this one stopped, because filling only nulls is idempotent. The process exits
non-zero when that happens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from _db_common import all_organization_ids

ROOT = Path(__file__).resolve().parent.parent

#: Written into every audit row this command produces (`actor_id` is NULL — no human did
#: this), and the `iss` this command claims on the service token it mints.
ACTING_AS = "reconcile-membership-links-cli"
ISSUER = "reconcile-membership-links"

#: The audience employee-service's service-token check compares against.
EMPLOYEE_AUDIENCE = "employee"

#: ADR-0008 §8 adopts `/internal/v1/hierarchy`'s contract unchanged (ADR-0006 §2.1).
PAGE_SIZE = 500
TOTAL_TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 2.0
MAX_ATTEMPTS = 5
#: Full jitter over 1, 2, 4, 8, 16 seconds.
BACKOFF_BASE_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0)
#: Between pages of one organization.
PAGE_PACE_MS = 100


class _DryRunRollback(Exception):
    """Raised inside an organization's transaction to force a rollback under `--dry-run`.

    Not an error — caught by the caller. Raising rather than calling `session.rollback()` and
    letting the `async with session.begin()` block exit normally is what guarantees the
    rollback: SQLAlchemy commits on a clean exit, so the only reliable way to make a
    transaction opened by a context manager end in a rollback is to leave it via an exception.
    Lifted verbatim from `tools/reconcile_permissions.py`, for the same reason.
    """


class RetryExhaustedError(RuntimeError):
    """Every attempt failed with a retryable error — a connection problem, timeout, or 5xx."""


@dataclass(frozen=True, slots=True)
class LinkRow:
    """One parsed row of the wire response. Two UUIDs, no PII (ADR-0008 §8)."""

    employee_id: uuid.UUID
    membership_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class LinkPage:
    items: list[LinkRow]
    next_cursor: uuid.UUID | None


class EmployeeLinkSource(Protocol):
    """What this command needs from employee-service. A test substitutes a stub."""

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = PAGE_SIZE
    ) -> LinkPage: ...


def _parse_row(raw: dict[str, Any]) -> LinkRow:
    """Parse at the boundary: two UUIDs or a refusal, never a string that looks like one."""
    membership_id = raw.get("membership_id")
    return LinkRow(
        employee_id=uuid.UUID(str(raw["employee_id"])),
        membership_id=uuid.UUID(str(membership_id)) if membership_id else None,
    )


class HttpEmployeeLinkSource:
    """The real client for `GET /internal/v1/employee-links`.

    Every number is ADR-0006 §2.1's, adopted unchanged by ADR-0008 §8. Two things are
    tighter than the hierarchy client `services/approval/app/employee_client.py`:

    * `iss` is `reconcile-membership-links`, which is the *only* issuer that endpoint's
      allowlist accepts — not a service name shared with anything on a request path.
    * the organization is **bound into the token** (`claims={"org": ...}`) on every call, so
      a captured token cannot be replayed against a different tenant than the one it was
      minted for. That is the gap the hierarchy endpoint documents as still open because its
      one real caller does not bind the claim; this caller closes it from day one.

    The residual risk is unchanged and worth restating: the internal token is a shared HMAC
    secret, so `iss` is a narrowing and not a cryptographic guarantee (ADR-0008 §8).
    """

    def __init__(self, base_url: str, settings: Any) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(TOTAL_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = PAGE_SIZE
    ) -> LinkPage:
        """One page, retried on connection errors, timeouts and 5xx only.

        A 4xx is raised on the first attempt: it means the audience, the secret, the issuer
        or the cursor is wrong, and retrying repeats the mistake.
        """
        from platform_core.internal_auth import mint_service_token

        params: dict[str, str | int] = {"organization_id": str(organization_id), "limit": limit}
        if cursor is not None:
            params["cursor"] = str(cursor)

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            # Minted per HTTP request rather than per page: `service_token_ttl_seconds` is 60
            # and the backoff schedule alone can exceed that within a single page's retries.
            headers = {
                "X-Internal-Token": mint_service_token(
                    self._settings,
                    audience=EMPLOYEE_AUDIENCE,
                    caller=ISSUER,
                    claims={"org": str(organization_id)},
                ),
                "X-Calling-Service": ISSUER,
            }
            try:
                response = await self._client.get(
                    "/internal/v1/employee-links", params=params, headers=headers
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < MAX_ATTEMPTS - 1:
                    await self._sleep(attempt)
                continue

            if response.status_code >= 500:
                last_error = httpx.HTTPStatusError(
                    f"{response.status_code} from employee-service",
                    request=response.request,
                    response=response,
                )
                if attempt < MAX_ATTEMPTS - 1:
                    await self._sleep(attempt)
                continue

            if response.status_code >= 400:
                response.raise_for_status()

            body = response.json()
            return LinkPage(
                items=[_parse_row(item) for item in body["items"]],
                next_cursor=(
                    uuid.UUID(str(body["next_cursor"])) if body.get("next_cursor") else None
                ),
            )

        raise RetryExhaustedError(
            f"employee-service did not answer after {MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    async def _sleep(self, attempt: int) -> None:
        base = BACKOFF_BASE_SECONDS[min(attempt, len(BACKOFF_BASE_SECONDS) - 1)]
        # Full jitter over a retry backoff, not a security decision — `random` is the right
        # tool here, not `secrets`.
        await asyncio.sleep(random.uniform(0, base))  # noqa: S311


async def fetch_links(
    source: EmployeeLinkSource, org_id: uuid.UUID, *, page_pace_ms: int = PAGE_PACE_MS
) -> list[LinkRow]:
    """Every employee of one organization, paged by cursor, **before any transaction opens**.

    ADR-0008 §8 says two things that cannot both be literally true — "one transaction per
    page on account's side" (inherited wholesale from ADR-0006 §2.1's contract table) and
    "one transaction per organization … one audit row per organization per run, in the same
    transaction". The audit rule and the `--dry-run` guarantee decide it: a run that commits
    per page cannot write one audit row carrying that run's counters, and cannot be rolled
    back as a unit. So the HTTP paging happens here, outside any transaction, and the writes
    happen in exactly one transaction per organization. Holding a database transaction open
    across an HTTP call — the third option — is the one nobody should want.

    The cost is bounded and small: a page is two UUIDs per row, so the largest realistic
    tenant (50k employees) is a few megabytes held for the length of one organization's run.
    """
    rows: list[LinkRow] = []
    cursor: uuid.UUID | None = None
    while True:
        page = await source.fetch_page(org_id, cursor=cursor, limit=PAGE_SIZE)
        rows.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            return rows
        if page_pace_ms > 0:
            await asyncio.sleep(page_pace_ms / 1000)


def _summary_line(result: Any, *, dry_run: bool) -> dict[str, Any]:
    """ADR-0008 §8's report line, plus the conflict detail an operator needs to act.

    The counters are exactly the six the ADR names. `conflicts` carries the ids behind two of
    them, because "three memberships disagree" is a statistic and "*these* three" is a work
    item — and every id here is a membership or employee UUID the caller of this command can
    already read.
    """
    return {
        "org_id": str(result.organization_id),
        "dry_run": dry_run,
        "links_written": result.links_written,
        "conflicts_employee_taken": result.conflicts_employee_taken,
        "conflicts_disagree": result.conflicts_disagree,
        "unrecognised_employee_ids": [str(e) for e in result.unrecognised_employee_ids],
        "unrecognised_membership_ids": [str(m) for m in result.unrecognised_membership_ids],
        "memberships_unlinked": result.memberships_unlinked,
        "conflicts": [
            {
                "kind": c.kind,
                "employee_id": str(c.employee_id),
                "membership_id": str(c.membership_id),
                "held_by_membership_id": (
                    str(c.held_by_membership_id) if c.held_by_membership_id else None
                ),
            }
            for c in result.conflicts
        ],
    }


async def reconcile_organization(
    org_id: uuid.UUID,
    source: EmployeeLinkSource,
    *,
    dry_run: bool,
    page_pace_ms: int = PAGE_PACE_MS,
) -> dict[str, Any]:
    """One organization: page employee-service, then one transaction on the app role.

    A clean exit commits; under `--dry-run` the transaction is always rolled back, whether or
    not anything was found. The tenant GUC is bound as the transaction's first statement and
    is transaction-local, so nothing leaks onto the pooled connection.
    """
    from app import services  # type: ignore[import-not-found]  # loaded from the service dir
    from platform_core.db import get_sessionmaker, set_tenant

    rows = await fetch_links(source, org_id, page_pace_ms=page_pace_ms)
    links = [
        services.EmployeeLink(employee_id=row.employee_id, membership_id=row.membership_id)
        for row in rows
    ]

    sessionmaker = get_sessionmaker()
    result = None
    try:
        async with sessionmaker() as session, session.begin():
            await set_tenant(session, org_id)
            result = await services.reconcile_employee_links(
                session, org_id, links, acting_as=ACTING_AS, dry_run=dry_run
            )
            if dry_run:
                raise _DryRunRollback()
    except _DryRunRollback:
        pass
    assert result is not None
    return _summary_line(result, dry_run=dry_run)


async def reconcile_fleet(
    org_ids: list[uuid.UUID],
    source: EmployeeLinkSource,
    *,
    dry_run: bool,
    pace_ms: int = 250,
    page_pace_ms: int = PAGE_PACE_MS,
) -> list[dict[str, Any]]:
    """Every organization in `org_ids`, sequentially, each its own transaction, paced.

    Stops at the first organization that raises rather than catching and moving on — see the
    module docstring for why that is the safe direction for a command whose every write is a
    null being filled.
    """
    results: list[dict[str, Any]] = []
    for index, org_id in enumerate(org_ids):
        results.append(
            await reconcile_organization(org_id, source, dry_run=dry_run, page_pace_ms=page_pace_ms)
        )
        is_last = index == len(org_ids) - 1
        if not is_last and pace_ms > 0:
            await asyncio.sleep(pace_ms / 1000)
    return results


def _fleet_summary(results: list[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
    links_written = sum(r["links_written"] for r in results)
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "organizations": len(results),
        "links_written_total": links_written,
        "conflicts_employee_taken_total": sum(r["conflicts_employee_taken"] for r in results),
        "conflicts_disagree_total": sum(r["conflicts_disagree"] for r in results),
        "unrecognised_employee_ids_total": sum(
            len(r["unrecognised_employee_ids"]) for r in results
        ),
        "unrecognised_membership_ids_total": sum(
            len(r["unrecognised_membership_ids"]) for r in results
        ),
        "memberships_unlinked_total": sum(r["memberships_unlinked"] for r in results),
    }
    # ADR-0008 §8: zero is the predicted outcome on this repository, so a non-zero one is the
    # finding — said in the output rather than left looking like a successful run.
    summary["finding"] = (
        None
        if links_written == 0
        else (
            f"{links_written} link(s) written: employee.employees.membership_id was populated "
            "by something outside this repository, which nothing here has ever sent. Review "
            "those rows before treating this run as routine (ADR-0008 §8)."
        )
    )
    return summary


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill and verify account.memberships.employee_id from employee-service's "
        "record of intent (ADR-0008 §8)"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--org", type=uuid.UUID, help="reconcile exactly one organization")
    target.add_argument(
        "--all", action="store_true", help="reconcile every organization in the fleet"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what each organization would get; write nothing",
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

    from app.config import settings  # type: ignore[import-not-found]
    from platform_core.db import dispose_engine, init_engine

    async def _run() -> int:
        cfg = settings()
        # The write engine: the NOBYPASSRLS app role, tenant bound per transaction. Redis is
        # deliberately *not* initialised — this command bumps no permission version, and a
        # connection it never uses is a dependency it should not have (ADR-0008 §8).
        init_engine(cfg)
        source = HttpEmployeeLinkSource(cfg.employee_service_url, cfg)

        try:
            org_ids = [args.org] if args.org else await all_organization_ids()
            if not org_ids:
                print(json.dumps({"organizations": 0}))
                return 0
            results = await reconcile_fleet(
                org_ids, source, dry_run=args.dry_run, pace_ms=args.pace
            )
        except Exception as exc:
            # One line an operator can read, and a non-zero exit. Organizations already
            # processed are already committed; the next run resumes from here.
            print(
                json.dumps({"error": type(exc).__name__, "detail": str(exc)[:500]}),
                file=sys.stderr,
            )
            return 1
        finally:
            await source.aclose()
            await dispose_engine()

        for line in results:
            print(json.dumps(line))
        print(json.dumps(_fleet_summary(results, dry_run=args.dry_run)))
        return 0

    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    _main()
