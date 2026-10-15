"""Reads for the employee service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.

The scope columns for an employee are:

    OWN      -> Employee.id                    (the caller's own record)
    TEAM     -> Employee.reporting_manager_id  (people who report to the caller)
    LOCATION -> Employee.location_code         (a single code column)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import BigInteger, Select, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, EmployeePii
from platform_core.auth import ScopeContext


def employees_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    department_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Select[Any]:
    """Scope-filtered employee list. `ctx.apply` is the enforcement point.

    Unlike account's membership list, LOCATION is a single-column comparison here, so the
    shared `apply` helper handles all three rungs directly — there is no CSV to unpack.
    """
    stmt = select(Employee).where(
        Employee.organization_id == ctx.organization_id,
        Employee.deleted_at.is_(None),
    )
    stmt = ctx.apply(
        stmt,
        owner_col=Employee.id,
        team_col=Employee.reporting_manager_id,
        location_col=Employee.location_code,
    )

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Employee.full_name).like(needle),
                func.lower(Employee.employee_no).like(needle),
                func.lower(func.coalesce(Employee.designation, "")).like(needle),
            )
        )
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    if status:
        stmt = stmt.where(Employee.status == status)

    return stmt.order_by(Employee.full_name)


async def get_employee(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> Employee | None:
    """Org-scoped fetch by id. Returns None on a tenant mismatch or a soft-deleted row —
    confirming an id exists in *some* organization is itself a disclosure."""
    stmt = select(Employee).where(
        Employee.id == employee_id,
        Employee.organization_id == org_id,
        Employee.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_employee_pii(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> EmployeePii | None:
    """The sealed PII row for one employee, or None when it has none.

    Org-scoped like every read here: the explicit `organization_id` filter sits beside row-level
    security, not in place of it. Returns the row with its sealed columns intact — this selector
    reads, it never decrypts; opening a value is `services.unmask_employee_pii`, and only there.
    """
    stmt = select(EmployeePii).where(
        EmployeePii.employee_id == employee_id,
        EmployeePii.organization_id == org_id,
        EmployeePii.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_employee_no(
    session: AsyncSession, org_id: uuid.UUID, employee_no: str
) -> Employee | None:
    """Looked up within one organization — `employee_no` is unique per org, not globally."""
    stmt = select(Employee).where(
        Employee.organization_id == org_id,
        Employee.employee_no == employee_no,
        Employee.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


GENERATED_NO_PREFIX = "EMP"


async def highest_generated_number(session: AsyncSession, org_id: uuid.UUID) -> int:
    """The largest N among this organization's `EMP<N>` employee numbers (0 when none).

    Soft-deleted rows count: the unique constraint covers them too, so a number a deleted record
    still holds must not be handed out again."""
    stmt = select(
        func.coalesce(
            func.max(
                cast(func.substr(Employee.employee_no, len(GENERATED_NO_PREFIX) + 1), BigInteger)
            ),
            0,
        )
    ).where(
        Employee.organization_id == org_id,
        Employee.employee_no.op("~")(f"^{GENERATED_NO_PREFIX}[0-9]{{1,12}}$"),
    )
    return int((await session.execute(stmt)).scalar_one())


async def direct_reports(
    session: AsyncSession, org_id: uuid.UUID, manager_id: uuid.UUID
) -> list[Employee]:
    """Everyone who reports directly to `manager_id`, within the organization.

    Read directly off `reporting_manager_id` rather than walked, so this is one query and a
    reporting cycle cannot make it loop.
    """
    stmt = (
        select(Employee)
        .where(
            Employee.organization_id == org_id,
            Employee.reporting_manager_id == manager_id,
            Employee.deleted_at.is_(None),
        )
        .order_by(Employee.full_name)
    )
    return list((await session.execute(stmt)).scalars().all())


def hierarchy_stmt(org_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int) -> Select[Any]:
    """Keyset page of every employee of one organization, in `id` order.

    Backs the internal backfill endpoint (ADR-0006 §2.1): `WHERE organization_id = :org AND
    id > :cursor ORDER BY id LIMIT n`, served by `uq_employees_org_id` — the same composite
    index the dotted-line FK needs, so pagination costs nothing extra. Offset pagination is
    deliberately not used here: a row inserted mid-run would shift every later page and
    silently skip an employee, which `platform_core.pagination` cannot protect against.
    """
    stmt = select(Employee).where(
        Employee.organization_id == org_id,
        Employee.deleted_at.is_(None),
    )
    if cursor is not None:
        stmt = stmt.where(Employee.id > cursor)
    return stmt.order_by(Employee.id).limit(limit)


def employee_links_stmt(org_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int) -> Select[Any]:
    """Keyset page of `(id, membership_id)` for **every** employee of one organization, linked or
    not, in `id` order (ADR-0008 §8).

    Two columns, selected by name rather than as whole entities: this backs an internal endpoint
    whose entire security argument is that it cannot return a name, an employee number or a
    sealed value, and `select(Employee)` would put every one of those on the wire regardless of
    what the response model then projects away. The narrowness is enforced here, in SQL, not left
    to a Pydantic model to remember.

    **No `membership_id IS NOT NULL` filter, deliberately.** One page serves both halves of the
    reconcile's job, and the rows with a null link are what make the second half work:

    - *fill* reads the non-null rows — the estate's only record of anybody's intent — and writes
      each into `account.memberships.employee_id`;
    - *verify* treats any `employee_id` account holds that does **not** appear in the page as an
      id employee does not recognise (ADR-0008 §4's mistyped UUID, and §8 calls this the only
      mechanism that finds it).

    Filtering to linked rows only would break verify, and break it worst where it matters most:
    an id pointing at a real but not-yet-linked employee would be absent from the page and so
    reported as junk — and during a rollout that population is precisely the one the reconcile
    exists to create. The cost of carrying the nulls is page size and nothing else; an unlinked
    row is still two UUIDs, still no PII, still strictly less than `hierarchy_stmt` returns.

    `uq_employees_org_membership` — the partial unique index this service keeps precisely for
    this read — guarantees at most one employee per membership, so fill never has to choose
    between two candidates for the same membership.

    Same pagination reasoning as `hierarchy_stmt`: keyset on `id`, served by `uq_employees_org_id`,
    never offset — a row inserted mid-run would shift every later page and silently skip a link.
    """
    stmt = select(Employee.id, Employee.membership_id).where(
        Employee.organization_id == org_id,
        Employee.deleted_at.is_(None),
    )
    if cursor is not None:
        stmt = stmt.where(Employee.id > cursor)
    return stmt.order_by(Employee.id).limit(limit)


#: Same value as approval's own `MAX_CHAIN_DEPTH` (`services/approval/app/domain.py`): a chain
#: 32 rungs deep is still routable, one deeper is not (ADR-0006 §5). Approval's walk starts
#: counting *at the employee* (depth 1, the requester's own row, not an approver rung) and can
#: render up to 32 rungs above that before it has to report `depth_exceeded` — 33 rows total.
#: This walk starts one row higher, at the *proposed* manager rather than the employee, so it
#: accounts for the employee's own row itself: accepting a proposed-manager chain up to
#: `MAX_MANAGER_CHAIN_DEPTH` long makes the resulting employee-inclusive chain at most
#: `MAX_MANAGER_CHAIN_DEPTH + 1` rows, matching approval's 33-row ceiling exactly.
MAX_MANAGER_CHAIN_DEPTH = 32


@dataclass(frozen=True, slots=True)
class ChainWalk:
    """The result of walking the *reporting* chain upward from a proposed manager
    (data model §4.3). `too_deep` and `existing_cycle` are defensive: the depth cap and the
    SQL-standard `CYCLE` clause are what terminate a walk that would otherwise loop forever,
    rather than an application-side visited set the next author could forget.

    The three reasons are distinct and mutually reportable — `reaches_employee` is a cycle this
    edit itself would create, `too_deep` is a chain this edit would make unroutable without
    looping, and `existing_cycle` is a cycle this walk found that already existed somewhere else
    in the data, before this edit. Callers should not collapse them into one message: only the
    first is actually "a reporting cycle".
    """

    reaches_employee: bool
    existing_cycle: bool
    too_deep: bool

    @property
    def refused(self) -> bool:
        return self.reaches_employee or self.existing_cycle or self.too_deep


#: One statement, run inside the same transaction as the change (data model §4.3). Walks the
#: *reporting* edges upward from the proposed manager and reports whether that walk reaches the
#: employee being edited, whether it found a cycle that already existed, or whether it ran past
#: `MAX_MANAGER_CHAIN_DEPTH` rungs — the depth boundary approval's own chain walk agrees with
#: (see `MAX_MANAGER_CHAIN_DEPTH`'s docstring for the off-by-one between where the two walks
#: start counting).
_CYCLE_CHECK_SQL = text(
    """
    WITH RECURSIVE up (id, manager_id, depth) AS (
            SELECT e.id, e.reporting_manager_id, 1
              FROM employee.employees e
             WHERE e.organization_id = :org_id
               AND e.id = :proposed_manager_id
        UNION ALL
            SELECT e.id, e.reporting_manager_id, up.depth + 1
              FROM up
              JOIN employee.employees e
                ON e.organization_id = :org_id
               AND e.id = up.manager_id
             WHERE up.depth <= :max_depth
               AND up.id <> :employee_id
    ) CYCLE id SET is_cycle USING path
    SELECT coalesce(bool_or(id = :employee_id), false) AS reaches_employee,
           coalesce(bool_or(is_cycle), false)          AS existing_cycle,
           coalesce(max(depth), 0) > :max_depth        AS too_deep
      FROM up
    """
)


async def walk_manager_chain(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    proposed_manager_id: uuid.UUID,
) -> ChainWalk:
    """Whether making `proposed_manager_id` a manager of `employee_id` would close a cycle, push
    the chain past `MAX_MANAGER_CHAIN_DEPTH`, or would write into a cycle that already exists
    elsewhere in the data — see `ChainWalk` for why these stay three separate booleans.

    Takes `pg_advisory_xact_lock` keyed on the organization first (ADR-0006 §2, data model
    §4.3): two concurrent edits on *disjoint* rows can each walk a chain that is acyclic at
    read time and still compose a cycle on commit (`A.mgr = B` and `C.mgr = D` closing a loop
    through `B..C`) — row locks on the two edited rows never overlap, so they do not prevent
    it. The lock is transaction-scoped, releases automatically at commit or rollback, and
    serialises hierarchy edits within this one organization only.
    """
    # Bound as a string, not cast in SQL (`:org_id::text` immediately after a bind parameter
    # is misparsed by asyncpg's prepared-statement path — verified against a live connection):
    # `||` needs both sides to already be text, and the parameter already is one.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('employee.hierarchy:' || :org_id, 0))"),
        {"org_id": str(org_id)},
    )
    row = (
        await session.execute(
            _CYCLE_CHECK_SQL,
            {
                "org_id": org_id,
                "proposed_manager_id": proposed_manager_id,
                "employee_id": employee_id,
                "max_depth": MAX_MANAGER_CHAIN_DEPTH,
            },
        )
    ).one()
    return ChainWalk(
        reaches_employee=bool(row.reaches_employee),
        existing_cycle=bool(row.existing_cycle),
        too_deep=bool(row.too_deep),
    )
