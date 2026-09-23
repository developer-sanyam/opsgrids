"""`summarize_exception` — what's safe to put on `/status`: a type name plus a short code,
never the free-text message that could carry a hostname, a DSN fragment or file contents.
"""

from __future__ import annotations

from platform_core.ops_monitor.redact import summarize_exception


class _WithErrno(Exception):
    def __init__(self, message: str, errno: int) -> None:
        super().__init__(message)
        self.errno = errno


class _WithSqlstate(Exception):
    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class _DriverError(Exception):
    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class _WrappedError(Exception):
    """Stands in for SQLAlchemy's own wrapping of a driver exception in `.orig`."""

    def __init__(self, message: str, orig: BaseException) -> None:
        super().__init__(message)
        self.orig = orig


def test_a_plain_exception_summarizes_to_its_type_name_only() -> None:
    assert summarize_exception(ValueError("smtp.internal.example refused the connection")) == (
        "ValueError"
    )


def test_the_message_never_appears_in_the_summary() -> None:
    summary = summarize_exception(ValueError("postgresql://user:hunter2@db.internal/opsgrids"))
    assert "hunter2" not in summary
    assert "db.internal" not in summary


def test_an_errno_is_included() -> None:
    assert summarize_exception(_WithErrno("connection refused", errno=61)) == "_WithErrno:61"


def test_a_direct_sqlstate_is_included() -> None:
    assert summarize_exception(_WithSqlstate("bad input", sqlstate="22001")) == (
        "_WithSqlstate:22001"
    )


def test_a_sqlstate_one_level_down_through_orig_is_included() -> None:
    """SQLAlchemy's own shape: the outer exception carries `.orig`, the real driver error."""
    driver_error = _DriverError("value too long", sqlstate="22001")
    wrapped = _WrappedError("(driver error) value too long", orig=driver_error)
    assert summarize_exception(wrapped) == "_WrappedError:22001"


def test_no_code_anywhere_is_just_the_type_name() -> None:
    assert summarize_exception(RuntimeError("something broke")) == "RuntimeError"
