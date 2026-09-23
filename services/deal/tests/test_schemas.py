"""m5: an explicit `null` on a PATCH field the database column never allows must be a 422 at
the boundary. Pure pydantic — no database needed to prove these.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import DealPatch


class TestDealPatchRejectsExplicitNulls:
    @pytest.mark.parametrize(
        "field",
        [
            "name",
            "company_id",
            "owner_id",
            "value_minor",
            "currency",
            "close_date",
            "probability",
            "source",
        ],
    )
    def test_explicit_null_is_refused(self, field: str) -> None:
        with pytest.raises(ValidationError):
            DealPatch(**{field: None})

    def test_primary_contact_id_may_be_explicitly_null(self) -> None:
        # A deal may genuinely have no primary contact.
        patch = DealPatch(primary_contact_id=None)
        assert "primary_contact_id" in patch.model_fields_set
