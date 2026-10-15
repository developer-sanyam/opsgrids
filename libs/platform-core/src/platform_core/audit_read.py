"""The organization's own audit trail, read back — one route per service, one shape everywhere.

Every service writes its audit rows into its **own** schema (`AuditLogMixin`), in the same
transaction as the change. There is no central audit store, and joining thirty schemas is the
one thing the platform's boundaries forbid (no cross-schema reads, so a later
database-per-service split stays a config change). So the read is the write's mirror image:
each service that is a source mounts `build_audit_router(...)`, which answers
`GET /api/v1/audit/<service>` from its own `audit_logs`, and the portal merges the pages it
gets back. Nothing new is stored, no event feed is added, and a service joins by mounting one
router.

What this route deliberately does **not** return:

* `before` / `after` snapshots. They hold the values a change replaced, and a reveal row's
  neighbours can hold contact data. The route returns the *names* of the fields a change
  touched, never a value. A PII reveal row (`entity.sensitive_read`) returns the names of the
  fields that were opened — who revealed what, and when — and nothing else.
* `ip`, `user_agent`, `session_id` and the operator's identity string. An organization admin
  sees *that* OpsGrids staff acted (`by_operator`), not which staff account or from where.

Authorization is `core.identity.audit.read` at ORG scope — the code `account` mints for "read the
audit trail", enforced here the same way billing enforces `core.billing.invoice.read`. The
query filters on the caller's `organization_id` itself; RLS is the net underneath, not the
control.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth import Scope, ScopeContext, require
from platform_core.db import get_tenant_session
from platform_core.errors import ValidationProblem
from platform_core.pagination import decode_keyset_cursor, encode_keyset_cursor

#: The permission every source enforces. Minted by `account` (`core` catalogue).
AUDIT_READ_PERMISSION = "core.identity.audit.read"

#: The action a PII reveal is recorded under (`record_sensitive_read`).
SENSITIVE_READ_ACTION = "entity.sensitive_read"

#: How an audit row names OpsGrids staff acting on an organization's behalf
#: (`acting_as=f"operator:{actor_id}"` in account and billing, `replay_dead_letters` in
#: notification). `acting_as` also carries system and service-to-service actors ("lead",
#: "payment", "crm-conversion-cleanup", "storefront-domain-sweep", ...): those are not staff, and
#: an entry they wrote must not read as "OpsGrids support acted".
OPERATOR_ACTING_AS_PREFIX = "operator:"

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

#: A field name as a snapshot key looks. Anything else — a key that is itself data, an email
#: used as a dict key — is dropped rather than echoed, because a key can carry a value too.
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: Snapshot keys that describe the row or the request rather than a field of the entity. A
#: reveal row stores the session and the sign-in method beside the field names; neither is a
#: field the actor touched.
_NOT_FIELDS = frozenset({"fields", "session_id", "amr"})


class AuditEntryOut(BaseModel):
    """One audit row, as the organization may read it. Values never leave the service."""

    id: uuid.UUID
    at: datetime
    service: str
    product: str
    #: The user id of the person who acted (`sub`), or `None` for a system action.
    actor_id: uuid.UUID | None
    #: True when OpsGrids staff acted on the organization's behalf.
    by_operator: bool
    action: str
    entity: str
    entity_id: uuid.UUID | None
    #: The names of the fields this entry touched — for a reveal, the fields that were opened.
    fields: list[str]
    #: True for a PII reveal (`entity.sensitive_read`).
    sensitive: bool
    reason: str | None


class AuditPageOut(BaseModel):
    service: str
    product: str
    items: list[AuditEntryOut]
    #: Pass back as `cursor` for the next, older page; `None` on the last one.
    next_cursor: str | None = None


class AuditFilters(BaseModel):
    """What the caller narrowed to. Every filter is optional and they AND together."""

    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    actor_id: uuid.UUID | None = None
    action: str | None = Field(default=None, max_length=100)
    entity: str | None = Field(default=None, max_length=80)


def audit_page_stmt(
    audit_model: type[Any],
    *,
    organization_id: uuid.UUID,
    filters: AuditFilters,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> Select[Any]:
    """Newest first, keyset-paged on `(at, id)`, filtered on the organization **in the query**.

    `id` is the tiebreaker because several rows routinely share one `at` (every row a request
    writes carries the same transaction timestamp); a cursor on `at` alone would skip or repeat
    them. One row past `limit` is fetched so the caller knows whether another page exists.
    """
    model = audit_model
    stmt = select(model).where(model.organization_id == organization_id)
    if filters.occurred_from is not None:
        stmt = stmt.where(model.at >= filters.occurred_from)
    if filters.occurred_to is not None:
        stmt = stmt.where(model.at < filters.occurred_to)
    if filters.actor_id is not None:
        stmt = stmt.where(model.actor_id == filters.actor_id)
    if filters.action is not None:
        stmt = stmt.where(model.action == filters.action)
    if filters.entity is not None:
        stmt = stmt.where(model.entity == filters.entity)
    if after is not None:
        after_at, after_id = after
        stmt = stmt.where(tuple_(model.at, model.id) < tuple_(literal(after_at), literal(after_id)))
    return stmt.order_by(model.at.desc(), model.id.desc()).limit(limit + 1)


def touched_fields(action: str, before: Any, after: Any) -> list[str]:
    """The field *names* an entry touched. Never a value.

    A reveal names its fields explicitly (`after = {"fields": [...]}`). Any other entry names
    the top-level keys whose value changed between the two snapshots — every key of a create's
    `after`, every key of a delete's `before`.
    """
    if action == SENSITIVE_READ_ACTION:
        named = after.get("fields") if isinstance(after, dict) else None
        if not isinstance(named, list):
            return []
        return sorted({f for f in named if isinstance(f, str) and _FIELD_NAME.match(f)})
    old = before if isinstance(before, dict) else {}
    new = after if isinstance(after, dict) else {}
    keys = set(old) | set(new)
    changed = {k for k in keys if old.get(k) != new.get(k) or (k in old) != (k in new)}
    return sorted(
        k for k in changed if isinstance(k, str) and _FIELD_NAME.match(k) and k not in _NOT_FIELDS
    )


def is_operator(acting_as: Any) -> bool:
    """True only for staff acting on the organization's behalf, never for a system actor."""
    return isinstance(acting_as, str) and acting_as.startswith(OPERATOR_ACTING_AS_PREFIX)


def to_entry(row: Any, *, service: str, product: str) -> AuditEntryOut:
    return AuditEntryOut(
        id=row.id,
        at=row.at,
        service=service,
        product=product,
        actor_id=row.actor_id,
        by_operator=is_operator(row.acting_as),
        action=row.action,
        entity=row.entity,
        entity_id=row.entity_id,
        fields=touched_fields(row.action, row.before, row.after),
        sensitive=row.action == SENSITIVE_READ_ACTION,
        reason=row.reason,
    )


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    raw_at, raw_id = decode_keyset_cursor(cursor, expected_parts=2)
    try:
        return datetime.fromisoformat(raw_at), uuid.UUID(raw_id)
    except ValueError as exc:
        raise ValidationProblem("Invalid cursor") from exc


async def read_audit_page(
    session: AsyncSession,
    audit_model: type[Any],
    ctx: ScopeContext,
    *,
    service: str,
    product: str,
    filters: AuditFilters,
    cursor: str | None,
    limit: int,
) -> AuditPageOut:
    """The selector. `ctx.apply` is a no-op at the ORG floor the route demands, and is called
    anyway so a future scope below ORG narrows to the caller's own actions instead of leaking."""
    for bound in (filters.occurred_from, filters.occurred_to):
        if bound is not None and bound.tzinfo is None:
            raise ValidationProblem(
                "`from` and `to` need a timezone offset, e.g. 2026-10-15T00:00:00Z"
            )
    if (
        filters.occurred_from is not None
        and filters.occurred_to is not None
        and filters.occurred_from >= filters.occurred_to
    ):
        raise ValidationProblem("`from` must be earlier than `to`")
    after = _decode_cursor(cursor) if cursor is not None else None
    stmt = audit_page_stmt(
        audit_model,
        organization_id=ctx.organization_id,
        filters=filters,
        after=after,
        limit=limit,
    )
    stmt = ctx.apply(stmt, owner_col=audit_model.actor_id)
    rows = list((await session.execute(stmt)).scalars().all())
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        encode_keyset_cursor(rows[-1].at.isoformat(), str(rows[-1].id)) if more and rows else None
    )
    return AuditPageOut(
        service=service,
        product=product,
        items=[to_entry(r, service=service, product=product) for r in rows],
        next_cursor=next_cursor,
    )


#: Module-level, not inside the factory: annotations are strings here (`from __future__`), and
#: FastAPI resolves them against this module's globals.
_SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
_AuditRead = Annotated[ScopeContext, Depends(require(AUDIT_READ_PERMISSION, minimum=Scope.ORG))]


def build_audit_router(audit_model: type[Any], *, service: str, product: str) -> APIRouter:
    """`GET /audit/<service>` (under `/api/v1`) over this service's own `audit_logs`.

    `product` is the product key the gateway gates the prefix on (`core` for an ungated
    service); it is echoed on every entry so the portal can label and filter by product.
    """
    router = APIRouter(tags=["audit"])

    @router.get(
        f"/audit/{service}",
        response_model=AuditPageOut,
        summary="The organization's audit trail recorded by this service, newest first",
        responses={
            403: {"description": "Missing core.identity.audit.read at ORG scope"},
            422: {"description": "A bad cursor, filter or date range"},
        },
    )
    async def list_audit_log(
        session: _SessionDep,
        ctx: _AuditRead,
        occurred_from: Annotated[
            datetime | None, Query(alias="from", description="Inclusive lower bound on `at`")
        ] = None,
        occurred_to: Annotated[
            datetime | None, Query(alias="to", description="Exclusive upper bound on `at`")
        ] = None,
        actor_id: Annotated[uuid.UUID | None, Query(description="The user who acted")] = None,
        action: Annotated[str | None, Query(max_length=100)] = None,
        entity: Annotated[str | None, Query(max_length=80)] = None,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    ) -> AuditPageOut:
        filters = AuditFilters(
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            actor_id=actor_id,
            action=action,
            entity=entity,
        )
        return await read_audit_page(
            session,
            audit_model,
            ctx,
            service=service,
            product=product,
            filters=filters,
            cursor=cursor,
            limit=limit,
        )

    return router
