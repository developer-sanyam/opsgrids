"""Request-schema validation helpers shared by every service's Pydantic contracts."""

from __future__ import annotations

from pydantic import BaseModel


def reject_explicit_null(instance: BaseModel, fields: tuple[str, ...]) -> None:
    """A PATCH field typed `X | None` is optional two different ways: omitted (leave alone) or
    explicitly `null` (the caller's own JSON says so). For a field the database column never
    allows NULL, only the first meaning is valid — the second is a malformed request (422), not a
    value that should reach an `IntegrityError` or a silent no-op write.

    Call it from a `model_validator(mode="after")` with the fields that map to NOT NULL columns.
    Fields that are genuinely nullable in the domain (an unassignable owner, an optional parent)
    are simply not passed.
    """
    for field in fields:
        if field in instance.model_fields_set and getattr(instance, field) is None:
            raise ValueError(f"{field} cannot be set to null")
