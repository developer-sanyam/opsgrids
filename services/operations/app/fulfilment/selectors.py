"""Reads for fulfilment (ADR-0024 §7, ADR-0025 §2/§3; `docs/fulfilment/plan.md` F3), plus the
ORM -> wire projections (`app.selectors`'s own convention: shaping a row for the wire is part of
reading it).

Like orders, workers and tasks are a shared operational queue — a dispatcher has to see every
task and every worker in the org to run the floor, not only the ones they personally touch — so
every grant behind `task.read`/`worker.read`/`pack.read` is ORG-only in `app/catalogue.py`, and
the routes that guard these lists additionally floor the permission itself at ORG (N3, security
review) — belt and suspenders against a custom role ever naming one of these at a narrower scope.
`workers_stmt`/`pick_tasks_stmt`/`packaging_materials_stmt`/`packages_stmt` still take the caller's
`ScopeContext` and call `ctx.apply()`, the same enforcement point every other scoped list in this
service uses, so that floor is never the *only* thing standing between an unexpected narrower
grant and the whole org's rows: `workers_stmt` has a real OWN reading (a worker's own row,
`Worker.employee_id`, matching `worker.write`'s own OWN default elsewhere in this module);
`pick_tasks_stmt`/`packaging_materials_stmt`/`packages_stmt` have none (no permission in this
catalogue ever defaults or tops out below ORG for them), so `apply()` is called with no owner
column — `Scope.OWN` on any of those three resolves to zero rows, never the whole organization's,
if it is ever reached at all. The one OWN-scoped permission this module has, `fulfilment.task
.execute`, is never used on a list either way: it guards single-object actions (accept a worker's
own offer, confirm a worker's own pick) checked with `ScopeContext.assert_can_touch`, never a
query filter.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.models import (
    AssignmentOffer,
    InventoryReservation,
    Location,
    Package,
    PackageLine,
    PackagingMaterial,
    PickException,
    PickTask,
    PickTaskLine,
    Worker,
    WorkerPresence,
    WorkerSkill,
    WorkerZoneAccess,
)
from app.schemas import (
    AssignmentOfferOut,
    PackageLineOut,
    PackageOut,
    PackagingMaterialOut,
    PickExceptionOut,
    PickTaskLineOut,
    PickTaskOut,
    WorkerOut,
)
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ workers


def workers_stmt(
    ctx: ScopeContext,
    *,
    warehouse_id: uuid.UUID | None = None,
    is_active: bool | None = None,
    q: str | None = None,
) -> Select[Any]:
    """N3 (security review): `ctx.apply` is the enforcement point (`OWN` reads the caller's own
    worker row, matched on `Worker.employee_id`), on top of `WorkerRead`'s own `minimum=Scope.ORG`
    floor."""
    stmt = select(Worker).where(
        Worker.organization_id == ctx.organization_id, Worker.deleted_at.is_(None)
    )
    stmt = ctx.apply(stmt, owner_col=Worker.employee_id)
    if warehouse_id:
        stmt = stmt.where(Worker.warehouse_id == warehouse_id)
    if is_active is not None:
        stmt = stmt.where(Worker.is_active == is_active)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(Worker.display_name).like(needle))
    return stmt.order_by(Worker.display_name.asc(), Worker.created_at.asc())


async def get_worker(
    session: AsyncSession, org_id: uuid.UUID, worker_id: uuid.UUID, *, for_update: bool = False
) -> Worker | None:
    stmt = select(Worker).where(
        Worker.id == worker_id, Worker.organization_id == org_id, Worker.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_worker_by_employee_id(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> Worker | None:
    """The caller's own worker row, resolved from `ScopeContext.principal.subject_ref` (the
    membership's `employee_id`, ADR-0000 §1) — how every OWN-scoped fulfilment action finds "my
    worker record" without a second identity system."""
    stmt = select(Worker).where(
        Worker.organization_id == org_id,
        Worker.employee_id == employee_id,
        Worker.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def skills_for(
    session: AsyncSession, org_id: uuid.UUID, worker_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not worker_ids:
        return {}
    stmt = select(WorkerSkill.worker_id, WorkerSkill.skill).where(
        WorkerSkill.organization_id == org_id, WorkerSkill.worker_id.in_(worker_ids)
    )
    out: dict[uuid.UUID, list[str]] = {}
    for worker_id, skill in await session.execute(stmt):
        out.setdefault(worker_id, []).append(skill)
    return out


async def zone_access_for(
    session: AsyncSession, org_id: uuid.UUID, worker_id: uuid.UUID
) -> list[uuid.UUID]:
    stmt = select(WorkerZoneAccess.location_id).where(
        WorkerZoneAccess.organization_id == org_id, WorkerZoneAccess.worker_id == worker_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def has_zone_access(
    session: AsyncSession, org_id: uuid.UUID, worker_id: uuid.UUID, location_id: uuid.UUID
) -> bool:
    stmt = select(WorkerZoneAccess.id).where(
        WorkerZoneAccess.organization_id == org_id,
        WorkerZoneAccess.worker_id == worker_id,
        WorkerZoneAccess.location_id == location_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def zone_access_map_for(
    session: AsyncSession, org_id: uuid.UUID, worker_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Every worker's own zone grants, grouped by worker — the batched sibling of
    `zone_access_for`/`has_zone_access` (N+1 avoidance), built for auto-assignment's own candidate
    pass (`app.fulfilment.services._worker_candidates`), which has to check every warehouse
    worker's zone access in one page, not one query per worker."""
    if not worker_ids:
        return {}
    stmt = select(WorkerZoneAccess.worker_id, WorkerZoneAccess.location_id).where(
        WorkerZoneAccess.organization_id == org_id, WorkerZoneAccess.worker_id.in_(worker_ids)
    )
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for worker_id, location_id in await session.execute(stmt):
        out.setdefault(worker_id, []).append(location_id)
    return out


async def worker_candidates_for_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID
) -> list[Worker]:
    """The raw pool auto-assignment ranks from — every active worker at this warehouse.
    Deliberately filters no further than `is_active`: presence, zone access and skills are the
    domain layer's job (`app.domain.is_worker_eligible_for_task`), never a second copy of that
    rule written again in SQL."""
    stmt = select(Worker).where(
        Worker.organization_id == org_id,
        Worker.warehouse_id == warehouse_id,
        Worker.deleted_at.is_(None),
        Worker.is_active.is_(True),
    )
    return list((await session.execute(stmt)).scalars().all())


async def offer_workload_for(
    session: AsyncSession, org_id: uuid.UUID, worker_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """How many **live** offers (`OFFERED`/`ACCEPTED`) each worker currently holds — the ranking's
    own "current open-task workload" (ADR-0024 §9). Reads `assignment_offers` rather than walking
    `pick_tasks` task-by-task: at most one live offer per task
    (`uq_assignment_offers_org_task_live`), so counting a worker's live offers **is** counting
    their open tasks, in one grouped query (N+1 avoidance)."""
    if not worker_ids:
        return {}
    stmt = (
        select(AssignmentOffer.worker_id, func.count(AssignmentOffer.id))
        .where(
            AssignmentOffer.organization_id == org_id,
            AssignmentOffer.worker_id.in_(worker_ids),
            AssignmentOffer.state.in_(("OFFERED", "ACCEPTED")),
        )
        .group_by(AssignmentOffer.worker_id)
    )
    out: dict[uuid.UUID, int] = {}
    for worker_id, count in await session.execute(stmt):
        out[worker_id] = count
    return out


async def last_offered_at_for(
    session: AsyncSession, org_id: uuid.UUID, worker_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Each worker's most recent `offered_at`, across every round whether it is still live or long
    since answered — the ranking's own idle-time tiebreak (ADR-0024 §9). A worker with no row here
    has never been offered anything, which `domain.rank_workers_for_offer` reads as "most idle of
    all"."""
    if not worker_ids:
        return {}
    stmt = (
        select(AssignmentOffer.worker_id, func.max(AssignmentOffer.offered_at))
        .where(AssignmentOffer.organization_id == org_id, AssignmentOffer.worker_id.in_(worker_ids))
        .group_by(AssignmentOffer.worker_id)
    )
    out: dict[uuid.UUID, datetime] = {}
    for worker_id, last_at in await session.execute(stmt):
        out[worker_id] = last_at
    return out


async def presence_for(
    session: AsyncSession, org_id: uuid.UUID, worker_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, WorkerPresence]:
    if not worker_ids:
        return {}
    stmt = select(WorkerPresence).where(
        WorkerPresence.organization_id == org_id, WorkerPresence.worker_id.in_(worker_ids)
    )
    return {p.worker_id: p for p in (await session.execute(stmt)).scalars().all()}


async def get_presence(
    session: AsyncSession, org_id: uuid.UUID, worker_id: uuid.UUID, *, for_update: bool = False
) -> WorkerPresence | None:
    stmt = select(WorkerPresence).where(
        WorkerPresence.organization_id == org_id, WorkerPresence.worker_id == worker_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ locations / zones


async def get_location_with_parent_chain(
    session: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> dict[uuid.UUID, Location]:
    """Every ancestor of `location_id`, keyed by id, walked one hop at a time up `parent_id` — the
    tree is shallow (zone -> aisle -> rack -> shelf -> bin, F1), so a handful of round trips beats
    a recursive CTE for one lookup."""
    out: dict[uuid.UUID, Location] = {}
    current_id: uuid.UUID | None = location_id
    while current_id is not None and current_id not in out:
        loc = (
            await session.execute(
                select(Location).where(
                    Location.id == current_id, Location.organization_id == org_id
                )
            )
        ).scalar_one_or_none()
        if loc is None:
            break
        out[loc.id] = loc
        current_id = loc.parent_id
    return out


async def resolve_zone_for_location(
    session: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> Location | None:
    """The nearest `kind == 'zone'` ancestor of `location_id`, or `None` when the tree never
    reaches one (a single-bin shop with no zone above it) — how pick tasks are grouped, "usually
    one per zone" (ADR-0025 §1)."""
    chain = await get_location_with_parent_chain(session, org_id, location_id)
    node = chain.get(location_id)
    while node is not None:
        if node.kind == "zone":
            return node
        node = chain.get(node.parent_id) if node.parent_id else None
    return None


async def resolve_kind_location(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID, kinds: Sequence[str]
) -> Location | None:
    """The first live location of one of `kinds` in this warehouse — `is_default` first, then
    `code`, so a warehouse with several staging bins resolves the same one every time. Tries each
    kind in order (`("pack_station", "dock")`, say) and returns the first match."""
    for kind in kinds:
        stmt = (
            select(Location)
            .where(
                Location.organization_id == org_id,
                Location.warehouse_id == warehouse_id,
                Location.kind == kind,
                Location.deleted_at.is_(None),
            )
            .order_by(Location.is_default.desc(), Location.code.asc())
            .limit(1)
        )
        found = (await session.execute(stmt)).scalar_one_or_none()
        if found is not None:
            return found
    return None


# ------------------------------------------------------------------ order reservations


async def reservations_by_order_line(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> dict[uuid.UUID, InventoryReservation]:
    """Every active-or-otherwise reservation `order_id`'s `accept` opened, keyed by the order
    line it covers. `ledger.reserve_many` keys each line's row `f"order:{id}:accept:v{version}:
    {line_id}"` (`app/orders/services.py::_reserve_lines_for`) — there is no line-level column to
    join on, so this reads the line id back off the tail of that key, which is stable regardless
    of which `version` the order was accepted at."""
    stmt = select(InventoryReservation).where(
        InventoryReservation.organization_id == org_id,
        InventoryReservation.reference_type == "order",
        InventoryReservation.reference_id == order_id,
    )
    out: dict[uuid.UUID, InventoryReservation] = {}
    for reservation in (await session.execute(stmt)).scalars().all():
        if not reservation.idempotency_key:
            continue
        tail = reservation.idempotency_key.rsplit(":", 1)[-1]
        try:
            line_id = uuid.UUID(tail)
        except ValueError:
            continue
        out[line_id] = reservation
    return out


async def destination_reservation_for(
    session: AsyncSession, org_id: uuid.UUID, task_line: PickTaskLine
) -> InventoryReservation | None:
    """The reservation a pick's transfer opened (or topped up) on `task_line`'s staging bucket —
    exactly the lookup the module docstring in `app/inventory/ledger.py` names: `balance_id ==`
    the destination bucket, `transferred_from_reservation_id ==` the source reservation, `status
    == 'active'`. This is what a pack confirmation transfers onward."""
    if task_line.reservation_id is None:
        return None
    stmt = select(InventoryReservation).where(
        InventoryReservation.organization_id == org_id,
        InventoryReservation.transferred_from_reservation_id == task_line.reservation_id,
        InventoryReservation.status == "active",
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ pick tasks


def pick_tasks_stmt(
    org_id: uuid.UUID,
    *,
    status: str | None = None,
    warehouse_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Org-scoped only — deliberately takes a plain `org_id`, not a `ScopeContext`. Unlike
    `workers_stmt`/`packages_stmt`/`packaging_materials_stmt` (list-route selectors only), this
    is also called from inside `app.fulfilment.services` for order-completeness checks
    (`_maybe_ready_order`, `create_pick_tasks_for_order`, `cancel_tasks_for_order`) using
    whichever permission's `ScopeContext` the calling *action* holds — often a worker's
    `task.execute:OWN` — which must always see every one of the order's own tasks regardless of
    that ctx's scope: applying `ctx.apply()` here would silently narrow those business-logic
    reads to "OWN", the wrong permission's scope entirely, not "every task on this order". The
    route-level list read (`GET /fulfilment/tasks`, N3, security review) applies its own
    `ctx.apply()` on top of this, in `app.api.routes.list_tasks` — the one call site this
    genuinely is a caller-facing list for."""
    stmt = select(PickTask).where(PickTask.organization_id == org_id, PickTask.deleted_at.is_(None))
    if status:
        stmt = stmt.where(PickTask.status == status)
    if warehouse_id:
        stmt = stmt.where(PickTask.warehouse_id == warehouse_id)
    if order_id:
        stmt = stmt.where(PickTask.order_id == order_id)
    return stmt.order_by(PickTask.created_at.desc())


async def get_pick_task(
    session: AsyncSession, org_id: uuid.UUID, task_id: uuid.UUID, *, for_update: bool = False
) -> PickTask | None:
    stmt = select(PickTask).where(
        PickTask.id == task_id, PickTask.organization_id == org_id, PickTask.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def lines_for_task(
    session: AsyncSession, org_id: uuid.UUID, task_id: uuid.UUID
) -> list[PickTaskLine]:
    stmt = (
        select(PickTaskLine)
        .where(PickTaskLine.organization_id == org_id, PickTaskLine.pick_task_id == task_id)
        .order_by(PickTaskLine.sequence.asc().nulls_last(), PickTaskLine.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def task_lines_for_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[PickTaskLine]:
    """Every pick-task line across every task an order has, joined through `pick_tasks.order_id`
    (`PickTaskLine` itself carries no `order_id` column). `app.dispatch.services.dispatch_order`
    reads this to tell a genuinely picked-and-packed line from one that never was (security
    review B1/M1) — `picked_milli`/`short_milli`/`quantity_milli` are the accounting a reservation
    walk cannot give on its own, and stay accurate even after a cancelled task's reservation is
    released back to stock (`app.fulfilment.services.cancel_task`)."""
    stmt = (
        select(PickTaskLine)
        .join(PickTask, PickTask.id == PickTaskLine.pick_task_id)
        .where(PickTaskLine.organization_id == org_id, PickTask.order_id == order_id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def lines_map_for_tasks(
    session: AsyncSession, org_id: uuid.UUID, task_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[PickTaskLine]]:
    """Every line for a set of tasks, grouped by task id — one query for a whole list page
    (N+1 avoidance, the same shape `app.orders.selectors.lines_map` uses)."""
    if not task_ids:
        return {}
    stmt = (
        select(PickTaskLine)
        .where(PickTaskLine.organization_id == org_id, PickTaskLine.pick_task_id.in_(task_ids))
        .order_by(PickTaskLine.sequence.asc().nulls_last(), PickTaskLine.created_at.asc())
    )
    out: dict[uuid.UUID, list[PickTaskLine]] = {}
    for line in (await session.execute(stmt)).scalars().all():
        out.setdefault(line.pick_task_id, []).append(line)
    return out


async def offers_map_for_tasks(
    session: AsyncSession, org_id: uuid.UUID, task_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[AssignmentOffer]]:
    if not task_ids:
        return {}
    stmt = (
        select(AssignmentOffer)
        .where(AssignmentOffer.organization_id == org_id, AssignmentOffer.task_id.in_(task_ids))
        .order_by(AssignmentOffer.offered_at.desc())
    )
    out: dict[uuid.UUID, list[AssignmentOffer]] = {}
    for offer in (await session.execute(stmt)).scalars().all():
        out.setdefault(offer.task_id, []).append(offer)
    return out


async def exceptions_map_for_tasks(
    session: AsyncSession, org_id: uuid.UUID, task_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[PickException]]:
    if not task_ids:
        return {}
    stmt = (
        select(PickException)
        .where(PickException.organization_id == org_id, PickException.task_id.in_(task_ids))
        .order_by(PickException.raised_at.desc())
    )
    out: dict[uuid.UUID, list[PickException]] = {}
    for exc in (await session.execute(stmt)).scalars().all():
        out.setdefault(exc.task_id, []).append(exc)
    return out


async def get_task_line(
    session: AsyncSession, org_id: uuid.UUID, line_id: uuid.UUID, *, for_update: bool = False
) -> PickTaskLine | None:
    stmt = select(PickTaskLine).where(
        PickTaskLine.id == line_id, PickTaskLine.organization_id == org_id
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def offers_for_task(
    session: AsyncSession, org_id: uuid.UUID, task_id: uuid.UUID
) -> list[AssignmentOffer]:
    """The task's whole offer history, newest first — what answers "why did this take forty
    minutes" (ADR-0025 §3)."""
    stmt = (
        select(AssignmentOffer)
        .where(AssignmentOffer.organization_id == org_id, AssignmentOffer.task_id == task_id)
        .order_by(AssignmentOffer.offered_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def excluded_workers_for_task(
    session: AsyncSession, org_id: uuid.UUID, task_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """Every worker ADR-0025 §2 excludes from this task's later **automatic** rounds — one whose
    offer on this exact task was `DECLINED`, `EXPIRED` or `RELEASED` (a "failed round"; `WITHDRAWN`
    is a dispatcher's own override, not a failure, so it does not exclude). A manual assignment is
    unaffected — a manager may still pick an excluded worker by hand (`offer_task`'s own
    `has_override` path never calls through this)."""
    stmt = select(AssignmentOffer.worker_id).where(
        AssignmentOffer.organization_id == org_id,
        AssignmentOffer.task_id == task_id,
        AssignmentOffer.state.in_(("DECLINED", "EXPIRED", "RELEASED")),
    )
    return frozenset((await session.execute(stmt)).scalars().all())


async def get_live_offer(
    session: AsyncSession, org_id: uuid.UUID, task_id: uuid.UUID
) -> AssignmentOffer | None:
    stmt = select(AssignmentOffer).where(
        AssignmentOffer.organization_id == org_id,
        AssignmentOffer.task_id == task_id,
        AssignmentOffer.state.in_(("OFFERED", "ACCEPTED")),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_offer(
    session: AsyncSession, org_id: uuid.UUID, offer_id: uuid.UUID
) -> AssignmentOffer | None:
    stmt = select(AssignmentOffer).where(
        AssignmentOffer.id == offer_id, AssignmentOffer.organization_id == org_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def exceptions_for_task(
    session: AsyncSession, org_id: uuid.UUID, task_id: uuid.UUID
) -> list[PickException]:
    stmt = (
        select(PickException)
        .where(PickException.organization_id == org_id, PickException.task_id == task_id)
        .order_by(PickException.raised_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_exception(
    session: AsyncSession, org_id: uuid.UUID, exception_id: uuid.UUID, *, for_update: bool = False
) -> PickException | None:
    stmt = select(PickException).where(
        PickException.id == exception_id, PickException.organization_id == org_id
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ packages


def packages_stmt(ctx: ScopeContext, *, order_id: uuid.UUID | None = None) -> Select[Any]:
    """N3 (security review): `ctx.apply` is the enforcement point — see `pick_tasks_stmt`'s own
    docstring; `pack.read` has the same all-ORG catalogue shape."""
    stmt = select(Package).where(
        Package.organization_id == ctx.organization_id, Package.deleted_at.is_(None)
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if order_id:
        stmt = stmt.where(Package.order_id == order_id)
    return stmt.order_by(Package.created_at.desc())


async def get_package(
    session: AsyncSession, org_id: uuid.UUID, package_id: uuid.UUID
) -> Package | None:
    stmt = select(Package).where(
        Package.id == package_id, Package.organization_id == org_id, Package.deleted_at.is_(None)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lines_for_package(
    session: AsyncSession, org_id: uuid.UUID, package_id: uuid.UUID
) -> list[PackageLine]:
    stmt = select(PackageLine).where(
        PackageLine.organization_id == org_id, PackageLine.package_id == package_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def packaged_milli_for_task_lines(
    session: AsyncSession, org_id: uuid.UUID, task_line_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """How much of each `pick_task_line` has already gone into a package — the over-pack guard,
    batched for every line a new package's payload names (N+1 avoidance)."""
    if not task_line_ids:
        return {}
    stmt = select(PackageLine.pick_task_line_id, PackageLine.quantity_milli).where(
        PackageLine.organization_id == org_id, PackageLine.pick_task_line_id.in_(task_line_ids)
    )
    out: dict[uuid.UUID, int] = {}
    for line_id, qty in await session.execute(stmt):
        out[line_id] = out.get(line_id, 0) + qty
    return out


def packaging_materials_stmt(ctx: ScopeContext) -> Select[Any]:
    """N3 (security review): `ctx.apply` is the enforcement point — see `pick_tasks_stmt`'s own
    docstring; `pack.read` has the same all-ORG catalogue shape."""
    stmt = select(PackagingMaterial).where(
        PackagingMaterial.organization_id == ctx.organization_id,
        PackagingMaterial.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=None)
    return stmt.order_by(PackagingMaterial.code.asc())


async def get_packaging_material(
    session: AsyncSession, org_id: uuid.UUID, material_id: uuid.UUID
) -> PackagingMaterial | None:
    stmt = select(PackagingMaterial).where(
        PackagingMaterial.id == material_id,
        PackagingMaterial.organization_id == org_id,
        PackagingMaterial.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_packaging_material_by_code(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> PackagingMaterial | None:
    stmt = select(PackagingMaterial).where(
        PackagingMaterial.organization_id == org_id,
        PackagingMaterial.code == code,
        PackagingMaterial.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ projections (ORM -> wire)


def worker_out(
    worker: Worker,
    *,
    skills: Sequence[str] = (),
    presence: WorkerPresence | None = None,
    zone_location_ids: list[str] | None = None,
) -> WorkerOut:
    """`zone_location_ids` is `None` when the caller does not also hold
    `operations.fulfilment.worker.location_read` — omitted rather than an empty list, so a client
    can tell "no zones granted yet" from "not allowed to see this"."""
    return WorkerOut(
        id=str(worker.id),
        employee_id=str(worker.employee_id),
        warehouse_id=str(worker.warehouse_id),
        display_name=worker.display_name,
        is_active=worker.is_active,
        skills=list(skills),
        presence_status=presence.status if presence else "offline",
        presence_last_seen_at=presence.last_seen_at if presence else None,
        zone_location_ids=zone_location_ids,
        created_at=worker.created_at,
        updated_at=worker.updated_at,
    )


def assignment_offer_out(offer: AssignmentOffer) -> AssignmentOfferOut:
    return AssignmentOfferOut(
        id=str(offer.id),
        task_id=str(offer.task_id),
        worker_id=str(offer.worker_id),
        round=offer.round,
        state=offer.state,
        score_breakdown=offer.score_breakdown,
        offered_at=offer.offered_at,
        expires_at=offer.expires_at,
        responded_at=offer.responded_at,
    )


def pick_exception_out(exc: PickException) -> PickExceptionOut:
    return PickExceptionOut(
        id=str(exc.id),
        task_id=str(exc.task_id),
        task_line_id=str(exc.task_line_id),
        reason=exc.reason,
        quantity=format_qty(exc.quantity_milli),
        note=exc.note,
        status=exc.status,
        raised_at=exc.raised_at,
        resolved_at=exc.resolved_at,
    )


def pick_task_line_out(line: PickTaskLine, *, sku: str) -> PickTaskLineOut:
    settled = line.picked_milli + line.short_milli >= line.quantity_milli
    if line.short_milli > 0:
        line_status = "short" if settled else "partial"
    elif settled:
        line_status = "picked"
    else:
        line_status = "pending"
    return PickTaskLineOut(
        id=str(line.id),
        order_line_id=str(line.order_line_id),
        inventory_item_id=str(line.inventory_item_id),
        sku=sku,
        source_location_id=str(line.source_location_id),
        destination_location_id=str(line.destination_location_id),
        quantity=format_qty(line.quantity_milli),
        picked=format_qty(line.picked_milli),
        short=format_qty(line.short_milli),
        sequence=line.sequence,
        status=line_status,
    )


def pick_task_out(
    task: PickTask,
    *,
    lines: Sequence[PickTaskLineOut] = (),
    offers: Sequence[AssignmentOfferOut] = (),
    exceptions: Sequence[PickExceptionOut] = (),
) -> PickTaskOut:
    return PickTaskOut(
        id=str(task.id),
        order_id=str(task.order_id),
        warehouse_id=str(task.warehouse_id),
        zone_location_id=str(task.zone_location_id) if task.zone_location_id else None,
        status=task.status,
        round=task.round,
        max_rounds=task.max_rounds,
        lines=list(lines),
        offers=list(offers),
        exceptions=list(exceptions),
        auto_assign_failure_reason=task.auto_assign_failure_reason,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def package_line_out(line: PackageLine, *, sku: str) -> PackageLineOut:
    return PackageLineOut(
        id=str(line.id),
        pick_task_line_id=str(line.pick_task_line_id),
        inventory_item_id=str(line.inventory_item_id),
        sku=sku,
        quantity=format_qty(line.quantity_milli),
    )


def package_out(package: Package, *, lines: Sequence[PackageLineOut] = ()) -> PackageOut:
    return PackageOut(
        id=str(package.id),
        order_id=str(package.order_id),
        packaging_material_id=(
            str(package.packaging_material_id) if package.packaging_material_id else None
        ),
        destination_location_id=str(package.destination_location_id),
        status=package.status,
        weight_grams=package.weight_grams,
        lines=list(lines),
        created_at=package.created_at,
    )


def packaging_material_out(material: PackagingMaterial) -> PackagingMaterialOut:
    return PackagingMaterialOut(
        id=str(material.id),
        code=material.code,
        name=material.name,
        length_mm=material.length_mm,
        width_mm=material.width_mm,
        height_mm=material.height_mm,
        max_weight_grams=material.max_weight_grams,
        tare_weight_grams=material.tare_weight_grams,
        is_active=material.is_active,
    )
