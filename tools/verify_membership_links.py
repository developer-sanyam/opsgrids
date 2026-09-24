#!/usr/bin/env python3
"""Verify that `employee.employees.membership_id` and `account.memberships.employee_id` agree —
and change nothing unless told to (R21; ADR-0008 §8 and its Decisions Q3 and Q4).

This is the **read-only-by-default face of `tools/reconcile_membership_links.py`**, built so the
nightly check ADR-0008 Q3 asks for cannot be misconfigured into a writer. The reconcile writes
unless `--dry-run` is added; this command reports unless `--apply` is added, so the safe behaviour
is the one nobody has to remember. It reads the same source over the same hardened internal
endpoint (`GET /internal/v1/employee-links`, `iss` `reconcile-membership-links`, the `org` claim
bound), pages it **outside** any transaction, and runs the same
`account.app.services.reconcile_employee_links` inside one tenant-bound transaction per
organization on the ordinary NOBYPASSRLS app role. Under the default that transaction **always
rolls back**, whether or not anything was found.

**What it reports**, per organization and in a fleet summary — every way the two halves can
disagree that anybody could act on:

* `links_written` — employee names a membership, and account has not linked it: a *fill
  candidate*. Under the default it is what `--apply` **would** write. Nothing in this repository
  has ever sent `membership_id` on employee create, so a non-zero number means something outside it
  did (ADR-0008 §8), and it is the number the column drop is waiting on.
* `conflicts_employee_taken` / `conflicts_disagree` — employee says E↔M, but another membership
  already holds E, or M already holds a different employee.
* `unrecognised_employee_ids` — an employee id account holds that employee-service has never
  heard of: ADR-0008 §4's mistyped UUID, the one thing that silently empties a person's OWN scope,
  and the reason this check is scheduled at all.
* `unrecognised_membership_ids` — employee names a membership account does not have or has revoked.

`memberships_unlinked` is printed for the operator but is **not** a finding: an active member with
no employee link is an ordinary state (a tenant with no HRMS, a contractor).

**Exit status**, so a scheduler can alert on it without parsing: `0` clean, `3` findings, `2` an
invocation refused (see `--apply`), `1` an error — an organization that raised stops the run and
the ones before it stay reported.

**`--apply`** is the reconcile's fill: it sets a null `employee_id` from employee's record and
never overwrites, never bumps `pv`, and writes one `membership.links_reconciled` audit row per
organization that changed, with `acting_as = verify-membership-links-cli`. It is explicit, it is
audited by the same code path the reconcile uses, and it is **refused when `CI` is set** — a
pipeline must not be able to write identity links, and the check that says so must not be
something a workflow file can switch off. A reviewed, human-run `--apply` is the only way it runs.

**The column-drop precondition** (ADR-0008 Q4). Under `--all` and without `--apply`, the summary
carries `column_drop_precondition`: `ready` only when the fleet-wide read-only run found no fill
candidate, no conflict and no unrecognised id. That is the evidence the owner's production dry-run
is meant to produce; it is what `docs/adr/0008-membership-id-column-drop.md` says to attach before
anyone runs the drop. This command never drops anything and nothing here schedules it.

Usage, from the repository root (point `DATABASE_URL` at the intended database first):

    uv run python tools/verify_membership_links.py --org <org_id>
    uv run python tools/verify_membership_links.py --all [--pace 250]
    uv run python tools/verify_membership_links.py --all --apply     # human only, never in CI

Scheduling: ADR-0008 Q3 puts the nightly run inside the orphan-check job R13 builds. That job does
not exist on this branch, so nothing schedules this yet; when it lands, the entry is
`python tools/verify_membership_links.py --all` and an exit status of `3` is the alert.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Mapping
from typing import Any

import reconcile_membership_links as reconcile

#: Written into the audit row of any write `--apply` makes (`actor_id` is NULL — no signed-in
#: person did this), so a `membership.links_reconciled` row says which command wrote it.
ACTING_AS = "verify-membership-links-cli"

EXIT_CLEAN = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2
EXIT_FINDINGS = 3

#: Values of `CI` that mean "not a pipeline". Anything else set is treated as one — the refusal
#: fails toward refusing, because the alternative is a write in a place nobody is watching.
_NOT_CI = frozenset({"", "0", "false", "no", "off"})


def refusal_for_apply(env: Mapping[str, str]) -> str | None:
    """Why `--apply` may not run in this environment, or `None` if it may.

    `CI` is set by GitHub Actions and by nearly every other pipeline; `GITHUB_ACTIONS` is checked
    as well because a workflow can `unset` one and forget the other.
    """
    for name in ("CI", "GITHUB_ACTIONS"):
        if env.get(name, "").strip().lower() not in _NOT_CI:
            return (
                f"--apply is refused when {name} is set: a pipeline must not write employee "
                "links. Run it by hand, after reading the read-only report."
            )
    return None


def findings(line: Mapping[str, Any]) -> list[str]:
    """What is wrong with one organization's report, as short reasons. Empty means clean."""
    reasons: list[str] = []
    if line["links_written"]:
        verb = "would be filled" if line["dry_run"] else "were filled"
        reasons.append(f"{line['links_written']} link(s) {verb} from employee's record")
    conflicts = line["conflicts_employee_taken"] + line["conflicts_disagree"]
    if conflicts:
        reasons.append(f"{conflicts} conflict(s) between the two records")
    if line["unrecognised_employee_ids"]:
        reasons.append(
            f"{len(line['unrecognised_employee_ids'])} employee id(s) account holds that "
            "employee-service does not recognise"
        )
    if line["unrecognised_membership_ids"]:
        reasons.append(
            f"{len(line['unrecognised_membership_ids'])} membership id(s) employee names that "
            "account does not have or has revoked"
        )
    return reasons


def summarise(results: list[dict[str, Any]], *, apply: bool, fleet: bool) -> dict[str, Any]:
    """The whole run in one line: the totals, whether it was clean, and the drop precondition."""
    per_org = {r["org_id"]: findings(r) for r in results}
    dirty = {org: reasons for org, reasons in per_org.items() if reasons}
    clean = not dirty

    blockers: list[str] = []
    if not fleet:
        blockers.append(
            "only one organization was checked; the drop needs a fleet-wide run (--all)"
        )
    if apply:
        blockers.append("this run wrote links (--apply); re-run read-only for the evidence")
    if not results:
        blockers.append("no organizations were checked")
    if not clean:
        blockers.append(f"{len(dirty)} organization(s) have findings")

    return {
        "mode": "apply" if apply else "verify",
        "read_only": not apply,
        "organizations": len(results),
        "clean": clean,
        "links_written_total" if apply else "fill_candidates_total": sum(
            r["links_written"] for r in results
        ),
        "conflicts_total": sum(
            r["conflicts_employee_taken"] + r["conflicts_disagree"] for r in results
        ),
        "unrecognised_employee_ids_total": sum(
            len(r["unrecognised_employee_ids"]) for r in results
        ),
        "unrecognised_membership_ids_total": sum(
            len(r["unrecognised_membership_ids"]) for r in results
        ),
        "memberships_unlinked_total": sum(r["memberships_unlinked"] for r in results),
        "organizations_with_findings": dirty,
        "column_drop_precondition": {"ready": not blockers, "blockers": blockers},
    }


def exit_status(summary: Mapping[str, Any]) -> int:
    return EXIT_CLEAN if summary["clean"] else EXIT_FINDINGS


async def verify(
    org_ids: list[uuid.UUID],
    source: reconcile.EmployeeLinkSource,
    *,
    apply: bool,
    fleet: bool,
    pace_ms: int = 250,
    page_pace_ms: int = reconcile.PAGE_PACE_MS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every organization in `org_ids`, sequentially, each in its own transaction.

    `dry_run` is `not apply` — there is no third state and no flag that forgets it — and the
    reconcile it delegates to rolls a dry run back unconditionally.
    """
    results = await reconcile.reconcile_fleet(
        org_ids,
        source,
        dry_run=not apply,
        pace_ms=pace_ms,
        page_pace_ms=page_pace_ms,
        acting_as=ACTING_AS,
    )
    return results, summarise(results, apply=apply, fleet=fleet)


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Report where employee.membership_id and account.employee_id disagree; "
        "writes nothing unless --apply (ADR-0008 §8, R21)"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--org", type=uuid.UUID, help="check exactly one organization")
    target.add_argument("--all", action="store_true", help="check every organization in the fleet")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="also fill null links from employee's record (audited; refused when CI is set)",
    )
    parser.add_argument(
        "--pace",
        type=int,
        default=250,
        metavar="MS",
        help="pause between organizations under --all, in milliseconds (default 250)",
    )
    args = parser.parse_args()

    if args.apply:
        refusal = refusal_for_apply(os.environ)
        if refusal is not None:
            print(json.dumps({"refused": refusal}), file=sys.stderr)
            raise SystemExit(EXIT_REFUSED)
        print(
            "verify-membership-links: --apply will WRITE account.memberships.employee_id",
            file=sys.stderr,
        )

    # The account service directory becomes the import root, exactly as its own processes run:
    # every service exposes a top-level `app`, so one process holds exactly one.
    sys.path.insert(0, str(reconcile.ROOT / "services" / "account"))

    from app.config import settings  # type: ignore[import-not-found]
    from platform_core.db import dispose_engine, init_engine

    async def _run() -> int:
        cfg = settings()
        # The write engine, used only under `--apply`: the NOBYPASSRLS app role, tenant bound per
        # transaction. Redis is deliberately not initialised — this bumps no permission version.
        init_engine(cfg)
        source = reconcile.HttpEmployeeLinkSource(cfg.employee_service_url, cfg)
        try:
            org_ids = [args.org] if args.org else await reconcile._all_organization_ids()
            if not org_ids:
                print(json.dumps({"organizations": 0}))
                return EXIT_CLEAN
            results, summary = await verify(
                org_ids, source, apply=args.apply, fleet=bool(args.all), pace_ms=args.pace
            )
        except Exception as exc:
            # One line an operator can read, and a non-zero exit. Organizations already reported
            # were already rolled back (or, under --apply, committed); a re-run starts over.
            print(
                json.dumps({"error": type(exc).__name__, "detail": str(exc)[:500]}),
                file=sys.stderr,
            )
            return EXIT_ERROR
        finally:
            await source.aclose()
            await dispose_engine()

        for line in results:
            print(json.dumps(line))
        print(json.dumps(summary))
        return exit_status(summary)

    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    _main()
