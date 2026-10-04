"""Keyset (cursor) pagination for the inventory list endpoints.

The platform's `paginate` is offset-based and runs a `COUNT(*)` over the whole statement — the
right default for an admin list, and the wrong one for a ledger that grows without bound. These
lists page on a **sort key + id** keyset instead: the cursor is the last row's `(sort value, id)`,
the next page is `WHERE (sort, id) < (:sort, :id)` and `LIMIT n + 1`, so page N costs the same as
page 1 and no total is computed. The row after the page is fetched only to learn whether there is
one; it is not returned.

A cursor is opaque to clients (urlsafe base64 of JSON) but **not trusted**: it is decoded and
type-checked on every request, and a malformed one is a 422, never a 500. It carries no tenant or
permission — scope is injected into the statement before the cursor condition is added, so a
forged cursor can only move within what the caller may already read.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.errors import ValidationProblem

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class CursorParams(BaseModel):
    cursor: str | None = None
    limit: int = DEFAULT_LIMIT


def cursor_params(
    cursor: Annotated[
        str | None,
        Query(max_length=300, description="Opaque cursor from the previous page's next_cursor."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> CursorParams:
    return CursorParams(cursor=cursor, limit=limit)


CursorQuery = Annotated[CursorParams, Depends(cursor_params)]


class CursorPage[T](BaseModel):
    """One page of a keyset-paginated list. `next_cursor` is `null` on the last page."""

    items: list[T]
    next_cursor: str | None = None


def encode_cursor(sort_value: datetime | str | int | uuid.UUID, row_id: uuid.UUID) -> str:
    kind = "dt" if isinstance(sort_value, datetime) else "s"
    value = sort_value.isoformat() if isinstance(sort_value, datetime) else str(sort_value)
    raw = json.dumps([kind, value, str(row_id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime | str, uuid.UUID]:
    """Parse a cursor back into `(sort value, id)`, or raise a 422."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        kind, value, row_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if kind == "dt":
            return datetime.fromisoformat(value), uuid.UUID(row_id)
        if kind == "s" and isinstance(value, str):
            return value, uuid.UUID(row_id)
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValidationProblem("That cursor is not valid") from exc
    raise ValidationProblem("That cursor is not valid")


async def keyset_page(
    session: AsyncSession,
    stmt: Select[Any],
    params: CursorParams,
    *,
    sort_col: Any,
    id_col: Any,
    sort_of: Callable[[Any], datetime | str],
    id_of: Callable[[Any], uuid.UUID],
    descending: bool = True,
    unique_rows: bool = True,
    parse_sort: Callable[[Any], Any] | None = None,
    sort_is_time: bool = True,
) -> tuple[list[Any], str | None]:
    """Fetch one keyset page of `stmt` ordered by `(sort_col, id_col)` — both descending or both
    ascending — and the cursor for the page after it (or `None`).

    `stmt` must already carry the tenant filter and the caller's scope; this only adds the cursor
    condition, the ordering and the limit. `sort_of`/`id_of` read the keyset values off a returned
    row (an ORM entity, or a `Row` for a multi-column select).
    """
    if params.cursor is not None:
        sort_value, row_id = decode_cursor(params.cursor)
        # A cursor minted by another list (a stock cursor on the ledger, say) or with a naive
        # timestamp must be a 422 here, not a type error from the driver.
        if isinstance(sort_value, datetime) != sort_is_time or (
            isinstance(sort_value, datetime) and sort_value.tzinfo is None
        ):
            raise ValidationProblem("That cursor is not valid")
        if parse_sort is not None:
            try:
                sort_value = parse_sort(sort_value)
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValidationProblem("That cursor is not valid") from exc
        after = tuple_(literal(sort_value), literal(row_id))
        stmt = stmt.where(
            tuple_(sort_col, id_col) < after if descending else tuple_(sort_col, id_col) > after
        )
    ordering = (sort_col.desc(), id_col.desc()) if descending else (sort_col.asc(), id_col.asc())
    stmt = stmt.order_by(None).order_by(*ordering).limit(params.limit + 1)
    result = await session.execute(stmt)
    rows: list[Any] = list(result.scalars().all() if unique_rows else result.all())
    page = list(rows[: params.limit])
    next_cursor = (
        encode_cursor(sort_of(page[-1]), id_of(page[-1])) if len(rows) > params.limit else None
    )
    return page, next_cursor


__all__ = [
    "CursorPage",
    "CursorParams",
    "CursorQuery",
    "decode_cursor",
    "encode_cursor",
    "keyset_page",
]
