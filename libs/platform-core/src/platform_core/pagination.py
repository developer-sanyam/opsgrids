"""Pagination.

Offset-based, which is the right default for admin lists and the wrong one for deep
scrolling — `paginate` counts and pages in one transaction so the total and the items
cannot disagree under concurrent writes.

Apply `ScopeContext.apply()` to the statement **before** paginating, or the count leaks
how many rows exist outside the caller's scope.
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


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
