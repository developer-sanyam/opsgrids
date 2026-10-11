"""Writes for BOMs and their versions.

**A version is mutable only while it is a draft.** Every edit below goes through `_draft` which
refuses a published or retired version with a 409 — and the same rule is enforced one layer down
by triggers on `bom_versions`, `bom_components` and `bom_substitutes`, so a write path that
forgot (or a person with a SQL console) cannot change what a production order was planned from.

**A BOM may not contain itself.** Adding a component or substitute, and publishing, walk the
published BOM graph from the produced item; reaching the item again is refused naming the loop.
The publish check is the safety net for two drafts that are each fine alone.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.manufacturing import common, domain, selectors
from app.manufacturing.models import Bom, BomComponent, BomSubstitute, BomVersion
from app.manufacturing.schemas import (
    BomComponentIn,
    BomComponentPatch,
    BomIn,
    BomPatch,
    BomSubstituteIn,
    BomVersionIn,
    BomVersionPatch,
)
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem

# ------------------------------------------------------------------ guards


async def get_bom_or_404(
    session: AsyncSession, org_id: uuid.UUID, bom_id: uuid.UUID, *, for_update: bool = False
) -> Bom:
    bom = await selectors.get_bom(session, org_id, bom_id, for_update=for_update)
    if bom is None:
        raise NotFoundProblem("That BOM is not part of this organization")
    return bom


async def get_version_or_404(
    session: AsyncSession,
    org_id: uuid.UUID,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> BomVersion:
    version = await selectors.get_version(
        session, org_id, version_id, bom_id=bom_id, for_update=for_update
    )
    if version is None:
        raise NotFoundProblem("That BOM version is not part of this BOM")
    return version


async def _draft(
    session: AsyncSession, org_id: uuid.UUID, bom_id: uuid.UUID, version_id: uuid.UUID
) -> tuple[Bom, BomVersion]:
    bom = await get_bom_or_404(session, org_id, bom_id)
    version = await get_version_or_404(session, org_id, bom_id, version_id, for_update=True)
    if version.status != "DRAFT":
        raise ConflictProblem(
            f"Version {version.version_no} is {version.status} and immutable — "
            "start a new version to change the recipe"
        )
    return bom, version


async def assert_no_cycle(
    session: AsyncSession,
    org_id: uuid.UUID,
    root_item_id: uuid.UUID,
    child_items: set[uuid.UUID],
) -> None:
    """Refuse if `root_item_id` can reach itself through `child_items` and the published BOMs.

    Serialised per organization: two BOMs publishing (or gaining a component) at once each see
    the other as a draft and would both pass, closing a cycle between them."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"bom-graph:{org_id}"}
    )
    edges: dict[str, set[str]] = {str(root_item_id): {str(c) for c in child_items}}
    frontier = set(child_items)
    seen: set[uuid.UUID] = {root_item_id}
    while frontier:
        seen |= frontier
        loaded = await selectors.edges_for_items(session, org_id, frontier)
        frontier = set()
        for parent, children in loaded.items():
            edges.setdefault(str(parent), set()).update(str(c) for c in children)
            frontier |= {c for c in children if c not in seen}
    path = domain.find_cycle(str(root_item_id), edges)
    if path is None:
        return
    items = await selectors.items_by_id(session, org_id, [uuid.UUID(p) for p in path])
    names = " -> ".join(items[uuid.UUID(p)].sku if uuid.UUID(p) in items else p for p in path)
    raise ConflictProblem(f"That would make a BOM contain itself: {names}")


async def _children(
    session: AsyncSession, org_id: uuid.UUID, version: BomVersion
) -> set[uuid.UUID]:
    comps = (await selectors.components_of(session, org_id, [version.id]))[version.id]
    subs = await selectors.substitutes_of(session, org_id, [c.id for c in comps])
    out = {c.component_item_id for c in comps}
    out.update(s.substitute_item_id for group in subs.values() for s in group)
    return out


# ------------------------------------------------------------------ BOM


async def create_bom(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: BomIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> tuple[Bom, bool]:
    org_id = ctx.organization_id
    claim = await common.claim(
        session, ctx, key, scope="bom.create", payload=payload.model_dump(mode="json")
    )
    if claim.replay and claim.result_id is not None:
        return await get_bom_or_404(session, org_id, claim.result_id), True

    item = await common.require_item(session, org_id, payload.item_id)
    output = common.qty(payload.output_quantity, item.uom, field="output_quantity")
    if await selectors.get_bom_for_item(session, org_id, item.id) is not None:
        raise ConflictProblem(f"{item.sku} already has a BOM — add a version to it instead")
    clash = (
        await session.execute(
            select(Bom.id).where(
                Bom.organization_id == org_id,
                Bom.code == payload.code,
                Bom.deleted_at.is_(None),
            )
        )
    ).first()
    if clash is not None:
        raise ConflictProblem(f"BOM code {payload.code} is already in use")

    bom = Bom(
        organization_id=org_id,
        code=payload.code,
        name=payload.name,
        item_id=item.id,
        description=payload.description,
        is_active=True,
        created_by=common.user_ref(ctx),
        updated_by=common.user_ref(ctx),
    )
    session.add(bom)
    await session.flush()
    version = BomVersion(
        organization_id=org_id,
        bom_id=bom.id,
        version_no=1,
        status="DRAFT",
        output_quantity_milli=output,
        notes="",
        created_by=common.user_ref(ctx),
        updated_by=common.user_ref(ctx),
    )
    session.add(version)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom.created",
        entity="bom",
        entity_id=bom.id,
        after={"code": bom.code, "item_id": str(item.id), "sku": item.sku},
        request=request,
    )
    claim.done(bom.id)
    return bom, False


async def update_bom(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    payload: BomPatch,
    *,
    request: Request | None = None,
) -> Bom:
    bom = await get_bom_or_404(session, ctx.organization_id, bom_id, for_update=True)
    before = {"name": bom.name, "description": bom.description, "is_active": bom.is_active}
    for name in payload.model_fields_set:
        setattr(bom, name, getattr(payload, name))
    bom.updated_by = common.user_ref(ctx)
    bom.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom.updated",
        entity="bom",
        entity_id=bom.id,
        before=before,
        after={"name": bom.name, "description": bom.description, "is_active": bom.is_active},
        request=request,
    )
    return bom


# ------------------------------------------------------------------ versions


async def create_version(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    payload: BomVersionIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> tuple[BomVersion, bool]:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"bom.version.create:{bom_id}",
        payload=payload.model_dump(mode="json"),
    )
    bom = await get_bom_or_404(session, org_id, bom_id, for_update=True)
    if claim.replay and claim.result_id is not None:
        return await get_version_or_404(session, org_id, bom_id, claim.result_id), True

    item = await common.require_item(session, org_id, bom.item_id)
    output = common.qty(payload.output_quantity, item.uom, field="output_quantity")
    common.check_window(payload.effective_from, payload.effective_to)
    existing = (await selectors.versions_of(session, org_id, [bom.id]))[bom.id]
    if any(v.status == "DRAFT" for v in existing):
        raise ConflictProblem(
            "This BOM already has a draft version — publish or discard it before starting another"
        )
    source: BomVersion | None = None
    if payload.copy_from_version_id is not None:
        source = await get_version_or_404(session, org_id, bom_id, payload.copy_from_version_id)

    version = BomVersion(
        organization_id=org_id,
        bom_id=bom.id,
        version_no=max((v.version_no for v in existing), default=0) + 1,
        status="DRAFT",
        output_quantity_milli=output,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        notes=payload.notes,
        created_by=common.user_ref(ctx),
        updated_by=common.user_ref(ctx),
    )
    session.add(version)
    await session.flush()
    copied = 0
    if source is not None:
        comps = (await selectors.components_of(session, org_id, [source.id]))[source.id]
        subs = await selectors.substitutes_of(session, org_id, [c.id for c in comps])
        for comp in comps:
            clone = BomComponent(
                organization_id=org_id,
                bom_version_id=version.id,
                line_no=comp.line_no,
                component_item_id=comp.component_item_id,
                quantity_milli=comp.quantity_milli,
                scrap_bps=comp.scrap_bps,
                effective_from=comp.effective_from,
                effective_to=comp.effective_to,
                notes=comp.notes,
                created_by=common.user_ref(ctx),
                updated_by=common.user_ref(ctx),
            )
            session.add(clone)
            await session.flush()
            for sub in subs.get(comp.id, []):
                session.add(
                    BomSubstitute(
                        organization_id=org_id,
                        bom_component_id=clone.id,
                        substitute_item_id=sub.substitute_item_id,
                        ratio_milli=sub.ratio_milli,
                        priority=sub.priority,
                        created_by=common.user_ref(ctx),
                        updated_by=common.user_ref(ctx),
                    )
                )
            copied += 1
        await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_version.created",
        entity="bom_version",
        entity_id=version.id,
        after={
            "bom_id": str(bom.id),
            "version_no": version.version_no,
            "copied_from": str(source.id) if source else None,
            "components_copied": copied,
        },
        request=request,
    )
    claim.done(version.id)
    return version, False


async def update_version(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: BomVersionPatch,
    *,
    request: Request | None = None,
) -> BomVersion:
    org_id = ctx.organization_id
    bom, version = await _draft(session, org_id, bom_id, version_id)
    before = {
        "output_quantity": version.output_quantity_milli,
        "effective_from": version.effective_from,
        "effective_to": version.effective_to,
        "notes": version.notes,
    }
    fields = payload.model_fields_set
    if "output_quantity" in fields and payload.output_quantity is not None:
        item = await common.require_item(session, org_id, bom.item_id)
        version.output_quantity_milli = common.qty(
            payload.output_quantity, item.uom, field="output_quantity"
        )
    if "effective_from" in fields:
        version.effective_from = payload.effective_from
    if "effective_to" in fields:
        version.effective_to = payload.effective_to
    if "notes" in fields and payload.notes is not None:
        version.notes = payload.notes
    common.check_window(version.effective_from, version.effective_to)
    version.updated_by = common.user_ref(ctx)
    version.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_version.updated",
        entity="bom_version",
        entity_id=version.id,
        before=before,
        after={
            "output_quantity": version.output_quantity_milli,
            "effective_from": version.effective_from,
            "effective_to": version.effective_to,
            "notes": version.notes,
        },
        request=request,
    )
    return version


async def discard_version(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> None:
    org_id = ctx.organization_id
    _, version = await _draft(session, org_id, bom_id, version_id)
    comps = (await selectors.components_of(session, org_id, [version.id]))[version.id]
    if comps:
        await session.execute(
            delete(BomSubstitute).where(
                BomSubstitute.organization_id == org_id,
                BomSubstitute.bom_component_id.in_([c.id for c in comps]),
            )
        )
        await session.execute(
            delete(BomComponent).where(
                BomComponent.organization_id == org_id,
                BomComponent.bom_version_id == version.id,
            )
        )
    await session.delete(version)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_version.discarded",
        entity="bom_version",
        entity_id=version_id,
        before={"bom_id": str(bom_id), "version_no": version.version_no},
        request=request,
    )


# ------------------------------------------------------------------ components


async def add_component(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: BomComponentIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> BomComponent:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"bom.component.add:{version_id}",
        payload=payload.model_dump(mode="json"),
    )
    if claim.replay and claim.result_id is not None:
        replayed = await selectors.get_component(session, org_id, claim.result_id)
        if replayed is not None:
            return replayed
    bom, version = await _draft(session, org_id, bom_id, version_id)
    item = await common.require_item(session, org_id, payload.component_item_id)
    quantity = common.qty(payload.quantity, item.uom)
    scrap = common.pct(payload.scrap_percent)
    common.check_window(payload.effective_from, payload.effective_to)
    children = await _children(session, org_id, version)
    await assert_no_cycle(session, org_id, bom.item_id, children | {item.id})
    line_no = (
        await session.execute(
            select(func.coalesce(func.max(BomComponent.line_no), 0)).where(
                BomComponent.organization_id == org_id, BomComponent.bom_version_id == version.id
            )
        )
    ).scalar_one() + 1
    component = BomComponent(
        organization_id=org_id,
        bom_version_id=version.id,
        line_no=line_no,
        component_item_id=item.id,
        quantity_milli=quantity,
        scrap_bps=scrap,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        notes=payload.notes,
        created_by=common.user_ref(ctx),
        updated_by=common.user_ref(ctx),
    )
    session.add(component)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_component.added",
        entity="bom_component",
        entity_id=component.id,
        after={
            "version_id": str(version.id),
            "line_no": line_no,
            "sku": item.sku,
            "quantity_milli": quantity,
            "scrap_bps": scrap,
        },
        request=request,
    )
    claim.done(component.id)
    return component


async def _component(
    session: AsyncSession, org_id: uuid.UUID, version: BomVersion, component_id: uuid.UUID
) -> BomComponent:
    component = await selectors.get_component(session, org_id, component_id, version_id=version.id)
    if component is None:
        raise NotFoundProblem("That component is not part of this BOM version")
    return component


async def update_component(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    component_id: uuid.UUID,
    payload: BomComponentPatch,
    *,
    request: Request | None = None,
) -> BomComponent:
    org_id = ctx.organization_id
    _, version = await _draft(session, org_id, bom_id, version_id)
    component = await _component(session, org_id, version, component_id)
    item = await common.require_item(session, org_id, component.component_item_id)
    before = {
        "quantity_milli": component.quantity_milli,
        "scrap_bps": component.scrap_bps,
        "effective_from": component.effective_from,
        "effective_to": component.effective_to,
        "notes": component.notes,
    }
    fields = payload.model_fields_set
    if "quantity" in fields and payload.quantity is not None:
        component.quantity_milli = common.qty(payload.quantity, item.uom)
    if "scrap_percent" in fields and payload.scrap_percent is not None:
        component.scrap_bps = common.pct(payload.scrap_percent)
    if "effective_from" in fields:
        component.effective_from = payload.effective_from
    if "effective_to" in fields:
        component.effective_to = payload.effective_to
    if "notes" in fields and payload.notes is not None:
        component.notes = payload.notes
    common.check_window(component.effective_from, component.effective_to)
    component.updated_by = common.user_ref(ctx)
    component.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_component.updated",
        entity="bom_component",
        entity_id=component.id,
        before=before,
        after={
            "quantity_milli": component.quantity_milli,
            "scrap_bps": component.scrap_bps,
            "effective_from": component.effective_from,
            "effective_to": component.effective_to,
            "notes": component.notes,
        },
        request=request,
    )
    return component


async def remove_component(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    component_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> None:
    org_id = ctx.organization_id
    _, version = await _draft(session, org_id, bom_id, version_id)
    component = await _component(session, org_id, version, component_id)
    await session.execute(
        delete(BomSubstitute).where(
            BomSubstitute.organization_id == org_id,
            BomSubstitute.bom_component_id == component.id,
        )
    )
    await session.delete(component)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_component.removed",
        entity="bom_component",
        entity_id=component_id,
        before={"version_id": str(version.id), "line_no": component.line_no},
        request=request,
    )


# ------------------------------------------------------------------ substitutes


async def add_substitute(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    component_id: uuid.UUID,
    payload: BomSubstituteIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> BomSubstitute:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"bom.substitute.add:{component_id}",
        payload=payload.model_dump(mode="json"),
    )
    if claim.replay and claim.result_id is not None:
        replayed = await selectors.get_substitute(session, org_id, claim.result_id, component_id)
        if replayed is not None:
            return replayed
    bom, version = await _draft(session, org_id, bom_id, version_id)
    component = await _component(session, org_id, version, component_id)
    item = await common.require_item(session, org_id, payload.substitute_item_id)
    if item.id == component.component_item_id:
        raise ValidationProblem("A substitute must be a different item from the component")
    ratio = common.qty(payload.ratio, field="ratio")
    clash = (
        await session.execute(
            select(BomSubstitute.id).where(
                BomSubstitute.organization_id == org_id,
                BomSubstitute.bom_component_id == component.id,
                BomSubstitute.substitute_item_id == item.id,
            )
        )
    ).first()
    if clash is not None:
        raise ConflictProblem(f"{item.sku} is already a substitute for this line")
    children = await _children(session, org_id, version)
    await assert_no_cycle(session, org_id, bom.item_id, children | {item.id})
    substitute = BomSubstitute(
        organization_id=org_id,
        bom_component_id=component.id,
        substitute_item_id=item.id,
        ratio_milli=ratio,
        priority=payload.priority,
        created_by=common.user_ref(ctx),
        updated_by=common.user_ref(ctx),
    )
    session.add(substitute)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_substitute.added",
        entity="bom_substitute",
        entity_id=substitute.id,
        after={"component_id": str(component.id), "sku": item.sku, "ratio_milli": ratio},
        request=request,
    )
    claim.done(substitute.id)
    return substitute


async def remove_substitute(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    component_id: uuid.UUID,
    substitute_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> None:
    org_id = ctx.organization_id
    _, version = await _draft(session, org_id, bom_id, version_id)
    component = await _component(session, org_id, version, component_id)
    substitute = await selectors.get_substitute(session, org_id, substitute_id, component.id)
    if substitute is None:
        raise NotFoundProblem("That substitute is not part of this component")
    await session.delete(substitute)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_substitute.removed",
        entity="bom_substitute",
        entity_id=substitute_id,
        before={"component_id": str(component.id)},
        request=request,
    )


# ------------------------------------------------------------------ publish / retire


async def _content_hash(session: AsyncSession, org_id: uuid.UUID, version: BomVersion) -> str:
    comps = (await selectors.components_of(session, org_id, [version.id]))[version.id]
    subs = await selectors.substitutes_of(session, org_id, [c.id for c in comps])
    document = {
        "output_quantity_milli": version.output_quantity_milli,
        "effective_from": version.effective_from.isoformat() if version.effective_from else None,
        "effective_to": version.effective_to.isoformat() if version.effective_to else None,
        "components": [
            {
                "line_no": c.line_no,
                "item": str(c.component_item_id),
                "quantity_milli": c.quantity_milli,
                "scrap_bps": c.scrap_bps,
                "effective_from": c.effective_from.isoformat() if c.effective_from else None,
                "effective_to": c.effective_to.isoformat() if c.effective_to else None,
                "substitutes": sorted(
                    [str(s.substitute_item_id), s.ratio_milli, s.priority]
                    for s in subs.get(c.id, [])
                ),
            }
            for c in comps
        ],
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def publish_version(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> tuple[BomVersion, bool]:
    org_id = ctx.organization_id
    claim = await common.claim(
        session, ctx, key, scope=f"bom.version.publish:{version_id}", payload={}
    )
    bom = await get_bom_or_404(session, org_id, bom_id)
    version = await get_version_or_404(session, org_id, bom_id, version_id, for_update=True)
    if claim.replay:
        return version, True
    if not domain.can_transition_bom_version(version.status, "PUBLISHED"):
        raise ConflictProblem(f"Version {version.version_no} is already {version.status}")
    if not bom.is_active:
        raise ConflictProblem("This BOM is inactive — reactivate it before publishing")
    comps = (await selectors.components_of(session, org_id, [version.id]))[version.id]
    if not comps:
        raise ConflictProblem("A BOM version needs at least one component before it is published")
    await assert_no_cycle(session, org_id, bom.item_id, await _children(session, org_id, version))
    version.content_hash = await _content_hash(session, org_id, version)
    version.status = "PUBLISHED"
    version.published_at = datetime.now(UTC)
    version.published_by = common.user_ref(ctx)
    version.updated_by = common.user_ref(ctx)
    version.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_version.published",
        entity="bom_version",
        entity_id=version.id,
        after={
            "bom_id": str(bom.id),
            "version_no": version.version_no,
            "content_hash": version.content_hash,
            "components": len(comps),
        },
        request=request,
    )
    claim.done(version.id)
    return version, False


async def retire_version(
    session: AsyncSession,
    ctx: ScopeContext,
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> BomVersion:
    org_id = ctx.organization_id
    claim = await common.claim(
        session, ctx, key, scope=f"bom.version.retire:{version_id}", payload={}
    )
    await get_bom_or_404(session, org_id, bom_id)
    version = await get_version_or_404(session, org_id, bom_id, version_id, for_update=True)
    if claim.replay:
        return version
    if not domain.can_transition_bom_version(version.status, "RETIRED"):
        raise ConflictProblem(
            f"Version {version.version_no} is {version.status}; "
            "only a published version can be retired"
        )
    version.status = "RETIRED"
    version.updated_by = common.user_ref(ctx)
    version.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="bom_version.retired",
        entity="bom_version",
        entity_id=version.id,
        after={"version_no": version.version_no},
        request=request,
    )
    claim.done(version.id)
    return version
