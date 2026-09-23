"""Boundary validation, no database — these are plain Pydantic checks, the same "no I/O, so
every case is a plain assertion" shape `test_domain.py` uses.

Each test here pins a 422 (a field-level validation problem) for input that would otherwise
either crash `domain.count_half_days` with a bare `OverflowError` deep past `date.max`, or reach
Postgres as a `NOT NULL` violation translated into an unhelpful generic 409 — see the validators'
own docstrings in `app/schemas.py` for the full reasoning.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError

from app.schemas import (
    MAX_LEAVE_DATE_HORIZON_DAYS,
    MAX_LEAVE_SPAN_DAYS,
    LeaveRequestIn,
    LeaveTypePatch,
)


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


class TestLeaveRequestDateSanity:
    def test_a_plain_near_term_request_is_accepted(self) -> None:
        today = _today()
        req = LeaveRequestIn(
            leave_type_id=uuid.uuid4(),
            start_date=today,
            end_date=today + dt.timedelta(days=1),
            reason="ordinary",
        )
        assert req.end_date == today + dt.timedelta(days=1)

    def test_a_start_date_far_beyond_the_horizon_is_rejected(self) -> None:
        today = _today()
        with pytest.raises(ValidationError, match="start_date must be within"):
            LeaveRequestIn(
                leave_type_id=uuid.uuid4(),
                start_date=today + dt.timedelta(days=MAX_LEAVE_DATE_HORIZON_DAYS + 1),
                end_date=today + dt.timedelta(days=MAX_LEAVE_DATE_HORIZON_DAYS + 2),
                reason="too far ahead",
            )

    def test_an_end_date_far_beyond_the_horizon_is_rejected(self) -> None:
        """`start_date` alone is within range, so the horizon check must still catch `end_date`
        on its own rather than only ever firing on the first field checked."""
        today = _today()
        with pytest.raises(ValidationError, match="end_date must be within"):
            LeaveRequestIn(
                leave_type_id=uuid.uuid4(),
                start_date=today,
                end_date=today + dt.timedelta(days=MAX_LEAVE_DATE_HORIZON_DAYS + 1),
                reason="too far ahead, end only",
            )

    def test_a_date_near_date_max_is_rejected_rather_than_crashing_later(self) -> None:
        """The exact input `domain.count_half_days`'s day-by-day loop would otherwise walk off
        the end of `date.max` on — this must be a 422 at the boundary, never a 500 deep inside
        the domain layer."""
        with pytest.raises(ValidationError):
            LeaveRequestIn(
                leave_type_id=uuid.uuid4(),
                start_date=dt.date.max - dt.timedelta(days=1),
                end_date=dt.date.max,
                reason="near the edge of what a date can represent",
            )

    def test_a_span_longer_than_the_cap_is_rejected(self) -> None:
        today = _today()
        with pytest.raises(ValidationError, match="cannot span more than"):
            LeaveRequestIn(
                leave_type_id=uuid.uuid4(),
                start_date=today,
                end_date=today + dt.timedelta(days=MAX_LEAVE_SPAN_DAYS + 1),
                reason="a multi-year single request",
            )

    def test_a_span_exactly_at_the_cap_is_accepted(self) -> None:
        today = _today()
        req = LeaveRequestIn(
            leave_type_id=uuid.uuid4(),
            start_date=today,
            end_date=today + dt.timedelta(days=MAX_LEAVE_SPAN_DAYS),
            reason="exactly at the cap",
        )
        assert (req.end_date - req.start_date).days == MAX_LEAVE_SPAN_DAYS


class TestLeaveTypePatchExplicitNull:
    """`name`, `is_paid` and `active` back `NOT NULL` columns. Omitting a key means "leave it
    alone"; sending it as an explicit JSON `null` must be a 422, not a database `IntegrityError`
    surfaced as an opaque 409."""

    @pytest.mark.parametrize("field,value", [("name", None), ("is_paid", None), ("active", None)])
    def test_an_explicit_null_is_rejected(self, field: str, value: None) -> None:
        with pytest.raises(ValidationError, match="may be omitted, but must not be null"):
            LeaveTypePatch.model_validate({field: value})

    def test_omitting_every_field_is_accepted(self) -> None:
        patch = LeaveTypePatch()
        assert patch.model_dump(exclude_unset=True) == {}

    def test_a_real_value_still_works_alongside_the_validator(self) -> None:
        patch = LeaveTypePatch.model_validate({"name": "Renamed", "is_paid": False})
        assert patch.model_dump(exclude_unset=True) == {"name": "Renamed", "is_paid": False}

    def test_annual_quota_may_still_be_explicitly_nulled(self) -> None:
        """Only the `NOT NULL`-backed trio is guarded — `annual_quota_half_days` and its
        siblings are nullable columns, so clearing one back to "no quota" is a legitimate write,
        not the shape this validator exists to catch."""
        patch = LeaveTypePatch.model_validate({"annual_quota_half_days": None})
        assert patch.model_dump(exclude_unset=True) == {"annual_quota_half_days": None}
