"""The audit trail stores what it is handed, including a row snapshot taken with `as_dict`.

Found by the cross-tenant sweep rather than by design. `update_organization` passed
`org.as_dict()` as its `before` snapshot — exactly what that method's docstring says it is for —
and every call then died inside the JSONB serializer, because a row snapshot holds UUIDs and
timestamps and `json.dumps` accepts neither. The route answered 500 to everyone, and nothing had
ever called it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.audit import REDACTED, record_audit


class _Row:
    """Stands in for a service's audit model: keeps whatever it was constructed with."""

    def __init__(self, **fields: Any) -> None:
        self.fields = fields


class _Session:
    def __init__(self) -> None:
        self.added: list[_Row] = []

    def add(self, row: _Row) -> None:
        self.added.append(row)


def _record(before: dict[str, Any]) -> dict[str, Any]:
    session = _Session()
    record_audit(
        cast(AsyncSession, session),
        _Row,
        organization_id=uuid.uuid4(),
        actor_id=None,
        action="organization.updated",
        entity="organization",
        before=before,
    )
    stored: dict[str, Any] = session.added[0].fields["before"]
    return stored


class TestSnapshotsAreStorable:
    def test_a_row_snapshot_with_uuids_timestamps_and_decimals_is_storable_json(self) -> None:
        """What `as_dict` produces must survive the column's serializer, at any depth."""
        row_id = uuid.uuid4()
        when = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
        stored = _record(
            {
                "id": row_id,
                "updated_at": when,
                "started_on": date(2026, 8, 22),
                "address": {"owner": row_id, "rent": Decimal("10.50")},
                "tags": [row_id],
            }
        )

        json.dumps(stored)  # the JSONB column's default serializer; raises on a raw UUID
        assert stored == {
            "id": str(row_id),
            "updated_at": when.isoformat(),
            "started_on": "2026-08-22",
            "address": {"owner": str(row_id), "rent": "10.50"},
            "tags": [str(row_id)],
        }

    def test_redaction_still_applies_to_a_coerced_snapshot(self) -> None:
        """Coercion must not reorder the pipeline so a secret slips through as a string."""
        stored = _record({"password": "hunter2", "nested": {"token": uuid.uuid4()}})
        assert stored["password"] == REDACTED
        assert stored["nested"]["token"] == REDACTED
