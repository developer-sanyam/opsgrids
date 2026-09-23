"""What is safe to put on `/status` — reachable, unauthenticated, to anything already on the
compose `back` network (and, for the certificate check, `front` too). That is a much larger
audience than this process's own logs, which only `docker logs`/`make edge-logs` (an operator
with box access) ever sees.

A raw exception message can carry a hostname, part of a DSN, a stack frame, or — for a
`ValueError` from parsing an unexpected file's contents — a fragment of that file. None of that
belongs on an HTTP endpoint nobody has to authenticate to reach. Every `Observation.detail` and
every field surfaced through `server.render_status` that originates from an exception goes
through `summarize_exception` instead of `str(exc)`; the full exception still reaches the
process's own structured logs unredacted, via `log.error(..., error=str(exc)[:300])`, which is
a deliberately different, more trusted audience.
"""

from __future__ import annotations


def summarize_exception(exc: BaseException) -> str:
    """`TypeName` plus a short code when the exception carries one — a driver's `errno` or a
    database's `sqlstate` (checked directly, and one level down through SQLAlchemy's own
    `.orig` wrapping of the underlying driver exception) — never the free-text message."""
    name = type(exc).__name__
    code = getattr(exc, "sqlstate", None) or getattr(exc, "errno", None)
    if code is None:
        orig = getattr(exc, "orig", None)
        if orig is not None:
            code = getattr(orig, "sqlstate", None) or getattr(orig, "errno", None)
    return f"{name}:{code}" if code else name
