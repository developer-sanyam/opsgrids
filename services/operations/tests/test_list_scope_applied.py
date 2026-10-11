"""Negative-authz: every list selector routes through `ctx.apply()`, not a bare org filter.

A caller whose scope is narrower than ORG (`owner_col=None` here — these resources have no
owner column) must match nothing; a statement that only filters `organization_id` would return
the whole tenant. Compiled, not executed: `ctx.apply` renders the `false` predicate.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.inventory import bulk_export
from app.jobwork import selectors as jobwork
from app.manufacturing import selectors as manufacturing
from app.quality import selectors as quality
from app.receiving import selectors as receiving
from platform_core.auth import Scope, ScopeContext
from tests.helpers import ctx_for

ORG = uuid.uuid4()
PERM = "operations.inventory.item.read"


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect())).lower()


def _ctx(scope: Scope) -> ScopeContext:
    return ctx_for(ORG, PERM, scope, subject_ref=uuid.uuid4())


STMTS = {
    "qc_templates": quality.templates_stmt,
    "inspections": quality.inspections_stmt,
    "jobwork_orders": jobwork.orders_stmt,
    "boms": manufacturing.boms_stmt,
    "production_orders": manufacturing.orders_stmt,
    "goods_receipts": receiving.receipts_stmt,
}


@pytest.mark.parametrize("name", sorted(STMTS))
def test_list_statement_applies_the_callers_scope(name: str) -> None:
    assert "false" in _sql(STMTS[name](_ctx(Scope.OWN)))
    assert "false" not in _sql(STMTS[name](_ctx(Scope.ORG)))


class _Result:
    def scalars(self) -> _Result:
        return self

    def __iter__(self) -> Any:
        return iter(())

    def all(self) -> list[Any]:
        return []


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result()


@pytest.mark.parametrize("entity", ["items", "locations"])
async def test_bulk_export_reference_reads_apply_the_callers_scope(entity: str) -> None:
    rec = _Recorder()
    await bulk_export.export_rows(rec, _ctx(Scope.OWN), entity)  # type: ignore[arg-type]
    assert "false" in _sql(rec.statements[0])
