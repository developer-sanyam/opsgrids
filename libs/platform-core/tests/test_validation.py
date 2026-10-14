"""`platform_core.validation.reject_explicit_null` — omitted is fine, explicit null is a 422."""

from __future__ import annotations

from typing import Self

import pytest
from pydantic import BaseModel, ValidationError, model_validator

from platform_core.validation import reject_explicit_null


class _Patch(BaseModel):
    name: str | None = None
    note: str | None = None  # genuinely nullable: never passed to the guard

    @model_validator(mode="after")
    def _guard(self) -> Self:
        reject_explicit_null(self, ("name",))
        return self


def test_an_omitted_field_is_left_alone() -> None:
    assert _Patch().model_fields_set == set()


def test_a_value_is_accepted() -> None:
    assert _Patch(name="x").name == "x"


def test_an_explicit_null_on_a_guarded_field_is_refused() -> None:
    with pytest.raises(ValidationError, match="name cannot be set to null"):
        _Patch.model_validate({"name": None})


def test_an_explicit_null_on_an_unguarded_field_is_allowed() -> None:
    assert _Patch.model_validate({"note": None}).note is None


def test_the_guard_names_the_first_offending_field() -> None:
    class Two(BaseModel):
        a: int | None = None
        b: int | None = None

    instance = Two.model_validate({"a": None, "b": None})
    with pytest.raises(ValueError, match="a cannot be set to null"):
        reject_explicit_null(instance, ("a", "b"))
