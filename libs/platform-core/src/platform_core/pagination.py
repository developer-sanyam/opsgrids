"""Pagination.

Offset-based, which is the right default for admin lists and the wrong one for deep
scrolling — `paginate` counts and pages in one transaction so the total and the items
cannot disagree under concurrent writes.

Apply `ScopeContext.apply()` to the statement **before** paginating, or the count leaks
how many rows exist outside the caller's scope.

Keyset cursors (`encode_keyset_cursor` / `decode_keyset_cursor`) are the alternative for lists
that read cross-tenant or grow without bound: the cursor is the ordering columns of the last row.
"""

from __future__ import annotations

import base64
import math
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.errors import ValidationProblem

#: Unit separator — never a legal character in a UUID, an ISO timestamp, a number or a
#: product/plan/meter key, so joining and splitting on it is unambiguous without escaping.
_CURSOR_SEP = "\x1f"


class PageParams(BaseModel):
    page: int = 1
    size: int = 25

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> PageParams:
    return PageParams(page=page, size=size)


PageQuery = Annotated[PageParams, Depends(page_params)]


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], *, total: int, params: PageParams) -> Page[T]:
        return cls(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            pages=max(1, math.ceil(total / params.size)) if total else 0,
        )


async def paginate(
    session: AsyncSession, stmt: Select[Any], params: PageParams
) -> tuple[list[Any], int]:
    """Count and fetch one page, in one transaction."""
    async with session.begin_nested() if session.in_transaction() else _null_ctx():
        total = (
            await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
        ).scalar_one()
        rows = (
            (await session.execute(stmt.offset(params.offset).limit(params.size))).scalars().all()
        )
    return list(rows), int(total)


class _null_ctx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


def encode_keyset_cursor(*parts: str) -> str:
    """Pack the ordering columns of the last row on a page into one opaque token."""
    return base64.urlsafe_b64encode(_CURSOR_SEP.join(parts).encode("utf-8")).decode("ascii")


def decode_keyset_cursor(cursor: str, *, expected_parts: int) -> list[str]:
    """Unpack a token `encode_keyset_cursor` produced.

    A cursor comes back from a client, so a stale, doctored or foreign one is a 422
    (`ValidationProblem`), not a 500 from an index error two frames later.
    """
    try:
        parts = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8").split(_CURSOR_SEP)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationProblem("Invalid cursor") from exc
    if len(parts) != expected_parts:
        raise ValidationProblem("Invalid cursor")
    return parts
