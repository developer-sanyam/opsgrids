"""`platform_core.db.constraint_name` / `sqlstate` — the driver-exception lookup the services
share. SQLAlchemy's asyncpg dialect wraps the driver exception, so the structured attribute can
sit on `exc.orig` or on `exc.orig.__cause__`; both must be found, `orig` first."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from platform_core.db import constraint_name, sqlstate


class _Driver(Exception):
    def __init__(self, **attrs: Any) -> None:
        super().__init__("driver")
        for key, value in attrs.items():
            setattr(self, key, value)


def _wrapped(orig: BaseException) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, orig)


def test_found_directly_on_orig() -> None:
    exc = _wrapped(_Driver(constraint_name="uq_a", sqlstate="23505"))
    assert constraint_name(exc) == "uq_a"
    assert sqlstate(exc) == "23505"


def test_found_on_the_cause_of_orig() -> None:
    orig = _Driver()
    orig.__cause__ = _Driver(constraint_name="ex_b", sqlstate="23P01")
    exc = _wrapped(orig)
    assert constraint_name(exc) == "ex_b"
    assert sqlstate(exc) == "23P01"


def test_orig_wins_over_cause() -> None:
    orig = _Driver(constraint_name="on_orig")
    orig.__cause__ = _Driver(constraint_name="on_cause")
    assert constraint_name(_wrapped(orig)) == "on_orig"


def test_none_when_the_driver_surfaces_nothing() -> None:
    orig = _Driver()
    orig.__cause__ = _Driver()
    exc = _wrapped(orig)
    assert constraint_name(exc) is None
    assert sqlstate(exc) is None


def test_none_when_there_is_no_cause_at_all() -> None:
    assert constraint_name(_wrapped(_Driver())) is None


def test_values_are_coerced_to_str() -> None:
    assert sqlstate(_wrapped(_Driver(sqlstate=23505))) == "23505"


@pytest.mark.parametrize("cls", [DBAPIError, IntegrityError])
def test_accepts_any_dbapi_error(cls: type[DBAPIError]) -> None:
    exc = cls("stmt", {}, _Driver(constraint_name="c"))
    assert constraint_name(exc) == "c"
