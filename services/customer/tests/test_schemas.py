"""m5: an explicit `null` on a PATCH field the database column never allows must be a 422 at
the boundary. Pure pydantic — no database needed to prove these.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import CompanyPatch, ContactPatch, OwnerPatch


class TestOwnerPatchRejectsExplicitNulls:
    @pytest.mark.parametrize("field", ["full_name", "email", "role", "is_active"])
    def test_explicit_null_is_refused(self, field: str) -> None:
        with pytest.raises(ValidationError):
            OwnerPatch(**{field: None})


class TestCompanyPatchRejectsExplicitNulls:
    @pytest.mark.parametrize(
        "field",
        [
            "name",
            "owner_id",
            "domain",
            "industry",
            "size_band",
            "city",
            "country",
            "status",
            "annual_revenue_minor",
            "currency",
        ],
    )
    def test_explicit_null_is_refused(self, field: str) -> None:
        with pytest.raises(ValidationError):
            CompanyPatch(**{field: None})


class TestContactPatchRejectsExplicitNulls:
    @pytest.mark.parametrize(
        "field", ["full_name", "title", "email", "company_id", "owner_id", "status"]
    )
    def test_explicit_null_is_refused(self, field: str) -> None:
        with pytest.raises(ValidationError):
            ContactPatch(**{field: None})
