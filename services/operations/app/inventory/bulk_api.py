"""HTTP surface for scanning, bulk import and export (inventory Phase 9, ADR-0027).

Mounted by `app.main` **ahead of** `app.api.routes.router` for the same reason
`app.inventory.api` is: `/operations/inventory/scan/...` and `/operations/inventory/data/...` are
static segments the catch-all `/operations/inventory/{item_id}` would otherwise swallow.

* `GET  /operations/inventory/scan?code=<payload>` — resolve a scanned label (`stock.read`).
* `GET  /operations/inventory/data/import/{entity}/template` — the columns of an import
  (`data.import`).
* `POST /operations/inventory/data/import/{entity}` — preview or commit, all-or-nothing
  (`data.import` **and** the entity's own write permission, ORG-floored, so the dangerous
  permission never grants more than the single-record endpoints would).
* `GET  /operations/inventory/data/export/{entity}` — every inventory list as JSON or CSV
  (`data.export` **and** the list's own read permission, ORG-floored); writes an audit row.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.routes import Entitled, IdempotencyKey, SessionDep
from app.inventory import bulk_export, bulk_import, scan
from app.inventory.bulk_schemas import (
    ExportEntity,
    ExportOut,
    ImportEntity,
    ImportIn,
    ImportResultOut,
    ImportTemplateOut,
    ScanOut,
)
from app.models import AuditLog
from platform_core.audit import record_audit
from platform_core.auth import Scope, ScopeContext, require
from platform_core.errors import ForbiddenProblem

router = APIRouter(tags=["inventory"])

StockRead = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.read", minimum=Scope.ORG))
]
DataImport = Annotated[
    ScopeContext, Depends(require("operations.inventory.data.import", minimum=Scope.ORG))
]
DataExport = Annotated[
    ScopeContext, Depends(require("operations.inventory.data.export", minimum=Scope.ORG))
]


def _also_needs(ctx: ScopeContext, permission: str) -> None:
    scope = ctx.principal.scope_for(permission)
    if scope is None or scope.rank < Scope.ORG.rank:
        raise ForbiddenProblem(f"Missing permission: {permission}")


@router.get(
    "/operations/inventory/scan",
    response_model=ScanOut,
    summary="Resolve a scanned label to its item, lot, roll or location",
    dependencies=[Entitled],
)
async def resolve_scan(
    # A query value, not a path segment: a code may hold `/..`, `?`, `#` or `%`, which a gateway
    # normalises or decodes in a path but passes through once percent-encoded in a query.
    payload: Annotated[str, Query(alias="code", min_length=1, max_length=200)],
    session: SessionDep,
    ctx: StockRead,
) -> ScanOut:
    return await scan.resolve(session, ctx.organization_id, ctx.principal, payload)


@router.get(
    "/operations/inventory/data/import/{entity}/template",
    response_model=ImportTemplateOut,
    summary="The columns an import of this entity takes",
    dependencies=[Entitled],
)
async def import_template(entity: ImportEntity, ctx: DataImport) -> ImportTemplateOut:
    _also_needs(ctx, bulk_import.WRITE_PERMISSION[entity])
    return bulk_import.template(entity)


@router.post(
    "/operations/inventory/data/import/{entity}",
    response_model=ImportResultOut,
    summary="Preview or commit a bulk import — all rows or none",
    dependencies=[Entitled],
)
async def import_data(
    entity: ImportEntity,
    payload: ImportIn,
    session: SessionDep,
    ctx: DataImport,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ImportResultOut:
    _also_needs(ctx, bulk_import.WRITE_PERMISSION[entity])
    result = await bulk_import.run_import(
        session, ctx, entity, payload, idempotency_key=idempotency_key, request=request
    )
    if payload.mode == "commit" and not result.committed:
        raise bulk_import.refused(result)
    return result


@router.get(
    "/operations/inventory/data/export/{entity}",
    response_model=ExportOut,
    summary="Export an inventory list as JSON (or CSV with ?format=csv)",
    dependencies=[Entitled],
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_data(
    entity: ExportEntity,
    session: SessionDep,
    ctx: DataExport,
    request: Request,
    fmt: Annotated[Literal["json", "csv"], Query(alias="format")] = "json",
) -> Response | ExportOut:
    _also_needs(ctx, bulk_export.READ_PERMISSION[entity])
    out = await bulk_export.export_rows(session, ctx, entity)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory.data.exported",
        entity="inventory_export",
        after={"entity": entity, "rows": len(out.rows), "truncated": out.truncated, "format": fmt},
        request=request,
    )
    if fmt == "csv":
        return Response(
            content=bulk_export.to_csv(out.columns, out.rows),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="inventory-{entity}.csv"',
                "X-Export-Truncated": "true" if out.truncated else "false",
            },
        )
    return out
